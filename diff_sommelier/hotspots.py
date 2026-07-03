"""Historical-hotspot rule (opt-in, --hotspots).

Where :mod:`~diff_sommelier.rules.surface` judges a hunk by *where* it lives and
:mod:`~diff_sommelier.blast_radius` by *how far* the change reaches, this rule
judges it by *how troubled the file's past is*. A file that changes constantly —
and, worse, keeps getting *fixed* — is where bugs breed. Michael Feathers called
these **hotspots**: high-churn, high-complexity files that reward extra reviewer
attention because history says they bite.

The idea:

1. **Mine ``git log`` once** for the file-change history of the working tree. For
   every commit we learn which files it touched and whether its message *looks
   like a fix* (``fix``/``bug``/``hotfix``/``revert``/``regression``…). From that
   we tally, per file, a **change count** (churn) and a **fix count**.

2. **Bucket a hunk's file by churn**, scaled against the repo's busiest file so
   the signal means the same thing in a 50-commit repo and a 50,000-commit one,
   with a small absolute floor so a brand-new repo doesn't light everything up.

3. **Emit a weighted signal** with a readable reason (e.g. *"hotspot: file
   changed in 37 commits (11 of them fixes)"*). A high **fix ratio** nudges the
   points up: a file that is not just busy but *repeatedly broken* is the real
   danger.

Everything here is **opt-in** (nothing runs unless ``--hotspots`` is passed) and
**local/offline** — it is just ``git log``. Outside a git repo (or with no
commit history) it gracefully **no-ops**: it emits no signals and never raises,
so the tool behaves exactly as before. The history is read a single time when
the index is built and cached on it, so scoring many hunks is cheap.

The public surface mirrors :mod:`~diff_sommelier.blast_radius`: :func:`make_rule`
binds a :class:`HotspotIndex` into a plain ``(Hunk, File) -> [Signal]`` rule,
:func:`build_index` builds an index rooted at a directory (or returns ``None``
when there is nothing to mine), and :func:`append_rule` lets the CLI wire the two
together while honouring ``[weights]``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

__all__ = [
    "RULE",
    "FileStats",
    "HotspotIndex",
    "build_index",
    "make_rule",
    "make_rule_or_none",
    "append_rule",
]

RULE = "hotspots"

# A commit whose subject/body matches this is treated as a *fix* (a signal that
# the file it touched was broken). Word-boundary, case-insensitive, and kept
# conservative: these tokens overwhelmingly mean "we repaired something". The
# point is to spot repeatedly-broken files, not to classify every commit.
_FIX_RE = re.compile(
    r"\b(?:fix(?:e[sd])?|fixing|bug(?:s|fix|fixes)?|hotfix|"
    r"revert(?:s|ed)?|regress(?:ion|ions|ed)?|patch(?:e[sd])?|"
    r"broke|broken|breakage|defect|issue|repair(?:s|ed)?)\b",
    re.I,
)

# Churn buckets: (min-share-of-busiest-file, min-absolute-commits, points, label).
# Checked high-to-low; the first row a file satisfies wins. A file must clear
# BOTH the relative share (so it's hot *for this repo*) and a tiny absolute floor
# (so a 2-commit repo doesn't flag everything). Points sit in the same ballpark
# as the surface/blast-radius rules so a hot file meaningfully lifts an otherwise
# quiet hunk without steam-rolling a genuine danger signal.
_BUCKETS: tuple[tuple[float, int, int, str], ...] = (
    (0.60, 6, 14, "a top change hotspot"),
    (0.30, 4, 10, "changes very frequently"),
    (0.15, 3, 6, "changes often"),
)

# If at least this fraction of a hotspot file's commits look like fixes, the file
# isn't just busy — it's repeatedly *broken*. We bump the bucket's points by
# :data:`_FIX_BONUS` and say so in the reason.
_FIX_RATIO_THRESHOLD = 0.34
_FIX_BONUS = 4

# Upper bound on commits parsed, so a repo with a very deep history can't make a
# single run pathological. Newest commits are the most relevant to today's churn.
_MAX_COMMITS = 5000

# Sentinel separating commits in our `git log` output. A commit record is:
#   <sep>\n<subject + body>\n<name-only file paths...>
# The separator is unlikely to occur naturally in a commit message.
_COMMIT_SEP = "\x1e--commit--\x1e"


@dataclass(frozen=True)
class FileStats:
    """Per-file history tally mined from ``git log``.

    Attributes:
        commits: Number of commits that touched this file (its churn).
        fixes: How many of those commits had a fix-looking message.
    """

    commits: int
    fixes: int

    @property
    def fix_ratio(self) -> float:
        """Share of this file's commits that looked like fixes (0.0 when none)."""
        return self.fixes / self.commits if self.commits else 0.0


@dataclass
class HotspotIndex:
    """A snapshot of a repo's per-file change history.

    Built by :func:`build_index`. ``stats`` maps a repo-relative, forward-slashed
    path to its :class:`FileStats`. ``max_commits`` is the churn of the busiest
    file, used to scale the buckets so "hot" is relative to *this* repo. Both are
    computed once from a single ``git log`` pass and cached here.
    """

    root: str
    stats: dict[str, FileStats] = field(default_factory=dict)
    max_commits: int = 0

    def stats_for(self, file_path: str) -> FileStats | None:
        """Look up the history for a hunk's file path, or ``None`` if untracked.

        The hunk path is normalized (back-slashes to forward-slashes, any leading
        ``./`` dropped) to match the keys ``git log`` produced. A file with no
        history in the index — a freshly added file, say — returns ``None`` so
        the rule cleanly emits nothing for it.
        """
        return self.stats.get(_norm_path(file_path))


def _norm_path(path: str) -> str:
    """Normalize a path to the forward-slashed, ``./``-free form git reports."""
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _looks_like_fix(message: str) -> bool:
    """True when a commit message reads like a fix/bug/revert."""
    return _FIX_RE.search(message) is not None


# The runner reads history for a root and yields (message, [paths]) per commit.
# Injectable so tests can exercise the whole tally + rule without a real repo.
HistoryRunner = Callable[[str], "Iterable[tuple[str, list[str]]]"]


def _git_log_records(root: str) -> Iterable[tuple[str, list[str]]] | None:
    """Yield ``(subject_body, [paths])`` per commit via ``git log`` (or ``None``).

    Uses one ``git log`` call with a custom record separator and ``--name-only``,
    so we learn each commit's message *and* the files it touched in a single
    subprocess. Returns ``None`` when git is unavailable or ``root`` is not a
    repository, signalling the caller to no-op. ``--no-merges`` keeps merge
    commits (which touch everything) from drowning the churn signal.
    """
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                root,
                "log",
                "--no-merges",
                f"--max-count={_MAX_COMMITS}",
                f"--format={_COMMIT_SEP}%n%B",
                "--name-only",
            ],
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):  # pragma: no cover - defensive
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return _parse_log(proc.stdout)


def _parse_log(output: str) -> list[tuple[str, list[str]]]:
    """Parse the ``git log`` blob into ``(message, [paths])`` per commit.

    Each record starts at :data:`_COMMIT_SEP`; the lines up to the first blank
    line after it are the commit message (subject + body), and the remaining
    non-empty lines are the ``--name-only`` file paths. Robust to the blank line
    git inserts between the message and the file list, and to messages that
    themselves contain blank lines.
    """
    records: list[tuple[str, list[str]]] = []
    # Split on the separator; the first chunk (before any separator) is empty.
    chunks = output.split(_COMMIT_SEP)
    for chunk in chunks:
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.split("\n")
        # git prints: message lines, one blank line, then name-only paths. But a
        # message can contain blank lines, so we can't just split on the first
        # one. Instead: a path is a line that names an existing-looking file
        # (no leading space, not empty). We treat the *trailing* run of such
        # lines (after the last blank line) as the file list.
        blank_idxs = [i for i, ln in enumerate(lines) if ln.strip() == ""]
        if blank_idxs:
            split_at = blank_idxs[-1]
            message = "\n".join(lines[:split_at])
            paths = [ln.strip() for ln in lines[split_at + 1 :] if ln.strip()]
        else:
            # No blank line: either a message-only commit (no files, e.g. an
            # empty commit) or a subject-only commit followed immediately by
            # files. Be conservative — if every line looks path-ish beyond the
            # first, treat the first as the subject and the rest as paths.
            message = lines[0]
            paths = [ln.strip() for ln in lines[1:] if ln.strip()]
        records.append((message, paths))
    return records


def _tally(records: Iterable[tuple[str, list[str]]]) -> tuple[dict[str, FileStats], int]:
    """Fold commit records into per-file (commits, fixes) and the busiest count."""
    commits: dict[str, int] = {}
    fixes: dict[str, int] = {}
    for message, paths in records:
        is_fix = _looks_like_fix(message)
        # De-dupe paths within a single commit so a weird record can't double-count.
        for path in dict.fromkeys(_norm_path(p) for p in paths if p):
            commits[path] = commits.get(path, 0) + 1
            if is_fix:
                fixes[path] = fixes.get(path, 0) + 1
    stats = {
        path: FileStats(commits=count, fixes=fixes.get(path, 0)) for path, count in commits.items()
    }
    max_commits = max(commits.values(), default=0)
    return stats, max_commits


def build_index(
    root: str | None = None, *, runner: HistoryRunner | None = None
) -> HotspotIndex | None:
    """Build a :class:`HotspotIndex` rooted at ``root`` (default: cwd).

    Returns ``None`` when there is nothing to mine — no readable directory, git
    unavailable, or a repo with no commit history — so the caller can cleanly
    skip the rule (the "gracefully no-op outside a repo" contract). ``runner`` is
    an injectable history source (used by tests); by default it shells out to
    ``git log``.
    """
    base = os.path.abspath(root or os.getcwd())
    if runner is None and not os.path.isdir(base):
        # Only the real git-log path needs an on-disk directory; an injected
        # runner supplies history directly (and is how tests drive this).
        return None

    records = runner(base) if runner is not None else _git_log_records(base)
    if records is None:
        return None
    stats, max_commits = _tally(records)
    if not stats or max_commits <= 0:
        return None
    return HotspotIndex(root=base, stats=stats, max_commits=max_commits)


def _bucket_for(stat: FileStats, max_commits: int) -> tuple[int, str] | None:
    """Map a file's history to (points, reason-label), or ``None`` if not hot.

    A file must clear both the relative share of the busiest file *and* the
    absolute floor for a bucket. When it qualifies, a high fix ratio bumps the
    points and appends a "repeatedly fixed" note to the label.
    """
    share = stat.commits / max_commits if max_commits else 0.0
    for min_share, min_abs, points, label in _BUCKETS:
        if share >= min_share and stat.commits >= min_abs:
            if stat.fixes and stat.fix_ratio >= _FIX_RATIO_THRESHOLD:
                return points + _FIX_BONUS, f"{label} and is repeatedly fixed"
            return points, label
    return None


def make_rule(index: HotspotIndex) -> Callable[[Hunk, File], Iterator[Signal]]:
    """Build a hotspots rule bound to ``index``.

    The returned callable matches the standard ``Rule`` shape
    (``(Hunk, File) -> Iterable[Signal]``). For each hunk it looks up the file's
    mined history and, when the file is a churn hotspot, yields a single weighted
    signal so hunks in historically bug-prone files float up the reading order.
    Files with no history (or below the churn threshold) yield nothing.
    """

    def score(hunk: Hunk, file: File) -> Iterator[Signal]:
        stat = index.stats_for(hunk.file_path)
        if stat is None:
            return
        bucket = _bucket_for(stat, index.max_commits)
        if bucket is None:
            return
        points, label = bucket
        commits_word = "commit" if stat.commits == 1 else "commits"
        if stat.fixes:
            fixes_word = "fix" if stat.fixes == 1 else "fixes"
            detail = f"{stat.commits} {commits_word}, {stat.fixes} {fixes_word}"
        else:
            detail = f"{stat.commits} {commits_word}"
        yield Signal(
            rule=RULE,
            points=points,
            reason=f"hotspot: file {label} ({detail})",
        )

    return score


def make_rule_or_none(
    index: HotspotIndex | None,
) -> Callable[[Hunk, File], Iterator[Signal]] | None:
    """Convenience: :func:`make_rule` when ``index`` is present, else ``None``.

    Lets the CLI express "add the rule only if we actually have history to use"
    without an ``if`` at the call site.
    """
    if index is None:
        return None
    return make_rule(index)


def append_rule(rules: Iterable, index: HotspotIndex | None, *, weight=None) -> list:
    """Return ``rules`` with the hotspots rule appended when ``index`` exists.

    A tiny helper so the CLI stays declarative: it hands over the config-built
    rule list and the (maybe-``None``) index and gets back the final list to
    score with. ``weight`` is an optional ``(name, rule) -> rule`` wrapper (the
    config's :meth:`~diff_sommelier.config.Config.apply_weight`) so a
    ``[weights]`` entry for ``hotspots`` tunes this rule like any other.
    """
    out = list(rules)
    rule = make_rule_or_none(index)
    if rule is not None:
        if weight is not None:
            rule = weight(RULE, rule)
        out.append(rule)
    return out
