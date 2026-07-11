"""CODEOWNERS-aware ownership rule (opt-in, --owners).

Where :mod:`~diff_sommelier.hotspots` judges a hunk by its file's *troubled
past* and :mod:`~diff_sommelier.blast_radius` by *how far* a change reaches,
this rule judges it by *who owns the ground it lands on*. A hunk is riskier when
it touches code the PR author does **not** own — and riskiest of all when the
file has **no owner at all**: nobody is watching it, so nobody will catch a
mistake there. This is textbook reviewer-side triage: float the changes landing
in unfamiliar (or un-watched) territory to the top of the reading order.

The idea:

1. **Parse the repo's ``CODEOWNERS``** once, from the three standard locations
   (``.github/CODEOWNERS``, ``CODEOWNERS``, ``docs/CODEOWNERS`` — first found
   wins, matching GitHub). Each non-comment line is ``<pattern> <owner...>``.

2. **Match a hunk's file** against the patterns with **last-match-wins**
   precedence (GitHub's rule): the *last* matching line in the file decides a
   path's owners. A path that matches nothing is **unowned**.

3. **Emit a weighted signal** when the file is owned by someone *other than* the
   author, or is unowned. A file the author owns yields nothing. Unowned files
   get a slightly higher bump than other-owned ones, since "nobody owns this" is
   the worse signal. Reasons read like ``"owned by @team-payments, not the
   author"`` or ``"no CODEOWNERS entry — unowned file"``.

Everything here is **opt-in** (nothing runs unless ``--owners`` is passed) and
**local/offline** — it is just a text file parse. With no CODEOWNERS file, or no
resolvable author, it gracefully **no-ops**: it emits no signals and never
raises, so the tool behaves exactly as before.

The public surface mirrors :mod:`~diff_sommelier.hotspots`: :func:`build_index`
parses the CODEOWNERS rooted at a directory (or returns ``None`` when there is
nothing to use), :func:`make_rule` binds an :class:`OwnersIndex` + author into a
plain ``(Hunk, File) -> [Signal]`` rule, and :func:`append_rule` lets the CLI
wire the two together while honouring ``[weights]``.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

__all__ = [
    "RULE",
    "OwnersRule",
    "OwnersIndex",
    "build_index",
    "make_rule",
    "make_rule_or_none",
    "append_rule",
]

RULE = "owners"

# Standard CODEOWNERS locations, in GitHub's precedence order: the first one that
# exists wins (the others are ignored).
_CODEOWNERS_LOCATIONS = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)

# Points for the two ownership-risk cases. "Unowned" is worse than "owned by
# someone else": at least an other-owner is *watching* the file. Kept in the same
# ballpark as the surface/hotspots rules so an ownership signal meaningfully lifts
# a quiet hunk without steam-rolling a genuine danger signal.
_POINTS_OTHER_OWNED = 8
_POINTS_UNOWNED = 12


def _norm_path(path: str) -> str:
    """Normalize a path to the forward-slashed, ``./``-free form for matching."""
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _norm_owner(owner: str) -> str:
    """Lower-case an owner token for author comparison, keeping the leading ``@``."""
    return owner.strip().lower()


@dataclass(frozen=True)
class OwnersRuleEntry:
    """One parsed CODEOWNERS line: a glob ``pattern`` and its ``owners``."""

    pattern: str
    owners: tuple[str, ...]


@dataclass
class OwnersIndex:
    """A parsed CODEOWNERS file: an ordered list of pattern -> owners rules.

    Built by :func:`build_index`. ``entries`` preserves file order so
    :meth:`owners_for` can apply GitHub's **last-match-wins** precedence.
    """

    root: str
    entries: list[OwnersRuleEntry] = field(default_factory=list)

    def owners_for(self, file_path: str) -> tuple[str, ...] | None:
        """Return the owners of ``file_path``, or ``None`` if the file is unowned.

        Applies last-match-wins: the *last* entry whose pattern matches decides
        the owners. A pattern that matches but lists no owners (a CODEOWNERS way
        to *clear* ownership for a path) yields an empty tuple, which callers
        treat as unowned too.
        """
        norm = _norm_path(file_path)
        matched: tuple[str, ...] | None = None
        for entry in self.entries:
            if _match(entry.pattern, norm):
                matched = entry.owners
        return matched


def _match(pattern: str, path: str) -> bool:
    """Match a CODEOWNERS ``pattern`` against a repo-relative ``path``.

    Implements the practical subset of GitHub's CODEOWNERS/gitignore semantics:

    * A trailing ``/`` matches everything under a directory.
    * A pattern with no slash (other than a trailing one) matches by *basename*
      anywhere in the tree (e.g. ``*.py`` or ``build``).
    * A leading ``/`` anchors to the repo root; otherwise a slashed pattern is
      still matched from the root (GitHub anchors slashed patterns).
    * ``*`` never crosses ``/`` for a single segment, but a bare ``*`` pattern
      matches everything.
    """
    pat = pattern.strip()
    if not pat:
        return False
    # A lone "*" (or "/*") owns everything.
    if pat in ("*", "/*"):
        return True

    anchored = pat.startswith("/")
    pat = pat.lstrip("/")

    # Directory pattern: "dir/" (or "dir") should match everything beneath it.
    dir_pattern = pat.endswith("/")
    pat_body = pat.rstrip("/")

    has_slash = "/" in pat_body

    if dir_pattern:
        # Match the directory itself and anything under it.
        return path == pat_body or path.startswith(pat_body + "/")

    if not has_slash:
        # Basename match anywhere in the tree (gitignore semantics).
        if fnmatch.fnmatch(path, pat_body):
            return True
        base = path.rsplit("/", 1)[-1]
        return fnmatch.fnmatch(base, pat_body)

    # Slashed pattern: anchor from root. Match the path exactly, or as a
    # directory prefix (so "src/api" also owns "src/api/handler.py").
    if fnmatch.fnmatch(path, pat_body):
        return True
    if path.startswith(pat_body + "/"):
        return True
    # Support a trailing "/*" style already handled; also allow "dir/**".
    if pat_body.endswith("/**") and path.startswith(pat_body[:-3]):
        return True
    return anchored and fnmatch.fnmatch(path, pat_body)


def _parse_codeowners(text: str) -> list[OwnersRuleEntry]:
    """Parse CODEOWNERS text into ordered :class:`OwnersRuleEntry` rows.

    Skips blank lines and ``#`` comments. Each remaining line is a whitespace-
    separated ``<pattern> [owner ...]``; a line with a pattern but no owners is
    kept (it *clears* ownership for that path under last-match-wins).
    """
    entries: list[OwnersRuleEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern = parts[0]
        owners = tuple(parts[1:])
        entries.append(OwnersRuleEntry(pattern=pattern, owners=owners))
    return entries


def _find_codeowners(base: str) -> str | None:
    """Return the text of the first standard CODEOWNERS file found, or ``None``."""
    for rel in _CODEOWNERS_LOCATIONS:
        candidate = os.path.join(base, rel)
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    return fh.read()
            except OSError:  # pragma: no cover - defensive
                return None
    return None


def build_index(root: str | None = None, *, text: str | None = None) -> OwnersIndex | None:
    """Build an :class:`OwnersIndex` rooted at ``root`` (default: cwd).

    Returns ``None`` when there is nothing to use — no readable directory and no
    injected ``text``, or no CODEOWNERS file with any entries — so the caller can
    cleanly skip the rule (the "gracefully no-op" contract). ``text`` is an
    injectable CODEOWNERS body (used by tests) that bypasses the filesystem.
    """
    base = os.path.abspath(root or os.getcwd())
    if text is None:
        if not os.path.isdir(base):
            return None
        text = _find_codeowners(base)
    if text is None:
        return None
    entries = _parse_codeowners(text)
    if not entries:
        return None
    return OwnersIndex(root=base, entries=entries)


# A rule bound to an index + author: the standard Rule shape.
OwnersRule = Callable[[Hunk, File], Iterator[Signal]]


def _fmt_owners(owners: tuple[str, ...]) -> str:
    """Render an owners tuple as a human phrase, e.g. ``@a`` or ``@a and @b``."""
    if len(owners) == 1:
        return owners[0]
    if len(owners) == 2:
        return f"{owners[0]} and {owners[1]}"
    return ", ".join(owners[:-1]) + f", and {owners[-1]}"


def make_rule(index: OwnersIndex, author: str) -> OwnersRule:
    """Build an owners rule bound to ``index`` and ``author``.

    The returned callable matches the standard ``Rule`` shape. For each hunk it
    resolves the file's CODEOWNERS owners and yields a single weighted signal
    when the file is owned by someone *other than* ``author`` (or is unowned).
    Files the author owns — or hunks with no file path — yield nothing.
    """
    author_norm = _norm_owner(author)

    def score(hunk: Hunk, file: File) -> Iterator[Signal]:
        path = hunk.file_path
        if not path:
            return
        owners = index.owners_for(path)
        if owners:
            owner_set = {_norm_owner(o) for o in owners}
            if author_norm in owner_set:
                # The author owns this file — no ownership risk to flag.
                return
            yield Signal(
                rule=RULE,
                points=_POINTS_OTHER_OWNED,
                reason=f"owned by {_fmt_owners(owners)}, not the author",
            )
        else:
            # No matching entry (or an ownership-clearing entry): unowned.
            yield Signal(
                rule=RULE,
                points=_POINTS_UNOWNED,
                reason="no CODEOWNERS entry — unowned file",
            )

    return score


def make_rule_or_none(index: OwnersIndex | None, author: str | None) -> OwnersRule | None:
    """:func:`make_rule` when both ``index`` and ``author`` are present, else ``None``.

    Lets the CLI express "add the rule only when we have both CODEOWNERS to read
    *and* an author to compare against" without an ``if`` at the call site.
    """
    if index is None or not author:
        return None
    return make_rule(index, author)


def append_rule(
    rules: Iterable, index: OwnersIndex | None, author: str | None, *, weight=None
) -> list:
    """Return ``rules`` with the owners rule appended when index + author exist.

    A tiny declarative helper for the CLI, mirroring
    :func:`diff_sommelier.hotspots.append_rule`. ``weight`` is an optional
    ``(name, rule) -> rule`` wrapper (the config's
    :meth:`~diff_sommelier.config.Config.apply_weight`) so a ``[weights]`` entry
    for ``owners`` tunes this rule like any other.
    """
    out = list(rules)
    rule = make_rule_or_none(index, author)
    if rule is not None:
        if weight is not None:
            rule = weight(RULE, rule)
        out.append(rule)
    return out
