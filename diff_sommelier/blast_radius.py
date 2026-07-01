"""Blast-radius rule (opt-in, --blast-radius).

Where :mod:`~diff_sommelier.rules.size` judges a hunk by *how much* changed and
:mod:`~diff_sommelier.rules.surface` by *where* it lives, this rule judges it by
*how far the change reaches*. A two-line edit to a function that is imported in
forty files is high-risk **precisely because** it is small — it slips past a
reviewer while quietly touching the whole codebase.

The idea:

1. **Extract changed symbol names** from a hunk. We look at both added and
   removed lines for *definitions* (``def``/``class``/``func``/``function``, and
   ``NAME =``-style assignments and exports) and pull out the identifier being
   defined. This is a deliberately conservative, language-agnostic regex pass —
   good enough to catch the common cases (Python, JS/TS, Go, Java-ish) without
   pulling in tree-sitter (that is a later backlog item).

2. **Count references across the working tree.** For each changed symbol we scan
   the rest of the repo for uses of that identifier (a word-boundary match),
   *excluding* the file the hunk lives in so a symbol's own definition/uses
   don't inflate its own blast radius. The scan prefers ``git ls-files`` for the
   file list (fast, and it honours ``.gitignore``); it falls back to a bounded
   ``os.walk`` when git isn't available.

3. **Emit a weighted signal** proportional to the usage count, with a readable
   reason (e.g. *"widely-used symbol changed: ``login`` referenced in 23 places"*).

Everything here is **opt-in** (nothing runs unless ``--blast-radius`` is passed)
and **local/offline** — it is just a filesystem scan. Outside a git repo (and
with no scannable files) it gracefully **no-ops**: it emits no signals and never
raises, so the tool behaves exactly as before.

The public surface is :func:`make_rule`, which binds a :class:`RepoIndex` (the
thing that can count references) into a plain ``(Hunk, File) -> [Signal]`` rule
matching the shape the rest of the rule pack uses. :func:`build_index` builds an
index rooted at a directory (or returns ``None`` when there is nothing to scan),
and the CLI wires the two together.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

__all__ = [
    "RULE",
    "RepoIndex",
    "extract_symbols",
    "build_index",
    "make_rule",
]

RULE = "blast-radius"

# Identifiers we never treat as "symbols" — matching them repo-wide would just
# measure how common a keyword is, not a real blast radius. Kept small and
# cross-language (the point is to drop obvious noise, not to be exhaustive).
_STOPWORDS = frozenset(
    {
        "if",
        "else",
        "elif",
        "for",
        "while",
        "return",
        "def",
        "class",
        "func",
        "function",
        "const",
        "let",
        "var",
        "import",
        "from",
        "export",
        "default",
        "public",
        "private",
        "protected",
        "static",
        "void",
        "int",
        "str",
        "bool",
        "true",
        "false",
        "none",
        "null",
        "self",
        "this",
        "new",
        "type",
        "interface",
        "package",
        "module",
        "async",
        "await",
        "try",
        "catch",
        "except",
        "finally",
        "with",
        "as",
        "in",
        "is",
        "and",
        "or",
        "not",
        "pass",
        "break",
        "continue",
        "get",
        "set",
        "data",
        "value",
        "key",
        "name",
        "id",
        "i",
        "j",
        "k",
        "x",
        "y",
    }
)

# Patterns that pull the *defined* identifier out of a changed line. Each pattern
# has the identifier in group 1. Applied to the content of added/removed lines
# (the leading +/- marker already stripped). Order does not matter; every match
# across every pattern contributes a candidate symbol.
_DEF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Python / general: `def name(` and `class Name(` / `class Name:`
    re.compile(r"\b(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    # Go / C-ish / JS: `func name(`, `function name(`
    re.compile(r"\b(?:func|function)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    # JS/TS declarations: `const name =`, `let name =`, `var name =`
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*="),
    # Bare top-level assignment / constant: `NAME =` (not `==`), used as a name.
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=(?!=)"),
    # Explicit exports: `export function name`, `export const name`,
    # `export class Name`, `export default name`.
    re.compile(
        r"\bexport\s+(?:default\s+)?(?:async\s+)?"
        r"(?:function|class|const|let|var)?\s*([A-Za-z_$][A-Za-z0-9_$]*)"
    ),
)

# Minimum identifier length we bother tracking. One/two-character names are
# almost always loop counters or locals whose repo-wide count is meaningless.
_MIN_SYMBOL_LEN = 3

# File extensions worth scanning for references. Keeping this to source-ish text
# avoids counting hits inside binaries/lockfiles and keeps the walk cheap.
_SCAN_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rb",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".pl",
        ".pm",
        ".lua",
        ".r",
        ".m",
        ".mm",
        ".vue",
        ".svelte",
        ".sql",
        ".gradle",
        ".groovy",
    }
)

# Directory names we never descend into during the os.walk fallback. Under git,
# ls-files already excludes these; this list only matters when git is absent.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tox",
        "target",
        ".idea",
        ".gradle",
        "vendor",
        ".next",
        ".cache",
    }
)

# Upper bound on files scanned in the os.walk fallback, so a huge non-git tree
# can't make a single run pathological. git ls-files paths are not capped (the
# repo is the intended, bounded universe).
_MAX_WALK_FILES = 5000

# Reference-count buckets -> points. Checked high-to-low; the first threshold the
# count meets wins. A handful of uses is unremarkable; dozens means the change
# reverberates and should be read early. Points are in the same ballpark as the
# surface rule so blast radius meaningfully lifts a small hunk without steam-
# rolling a genuine danger signal. The label is woven into a readable reason.
_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (40, 16, "reverberates across the codebase"),
    (15, 11, "is widely used"),
    (5, 6, "is used in several places"),
)


@dataclass
class RepoIndex:
    """A scannable snapshot of a working tree that can count symbol references.

    Built by :func:`build_index`. ``files`` is the list of absolute paths that
    make up the searchable universe (already filtered to text-ish source files).
    The per-symbol counts are memoized so scoring many hunks that mention the
    same symbol only pays the scan once.
    """

    root: str
    files: list[str] = field(default_factory=list)
    _cache: dict[str, int] = field(default_factory=dict)
    # Injectable reader, so tests can exercise counting without touching disk.
    _read: object = None

    def _read_text(self, path: str) -> str:
        if self._read is not None:
            return self._read(path)  # type: ignore[operator]
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                return fh.read()
        except OSError:  # pragma: no cover - defensive (races/permissions)
            return ""

    def count(self, symbol: str, *, exclude: str | None = None) -> int:
        """Count word-boundary references to ``symbol`` across the tree.

        ``exclude`` is an absolute path whose hits are ignored — used to drop the
        symbol's *own* file so a definition doesn't count as its own blast radius.
        Results (without the exclusion) are memoized; the exclusion is applied on
        top, so different callers excluding different files still share the scan.
        """
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        total = 0
        excl_norm = os.path.normpath(exclude) if exclude else None
        for path in self.files:
            hits = self._cache.get((symbol, path))  # type: ignore[call-overload]
            if hits is None:
                hits = len(pattern.findall(self._read_text(path)))
                self._cache[(symbol, path)] = hits  # type: ignore[index]
            if excl_norm is not None and os.path.normpath(path) == excl_norm:
                continue
            total += hits
        return total


def _clean_line(line: str) -> str:
    """Strip a unified-diff marker (+/-/space) from a body line."""
    if line[:1] in {"+", "-", " "}:
        return line[1:]
    return line


def extract_symbols(hunk: Hunk) -> list[str]:
    """Pull candidate *defined* symbol names out of a hunk.

    Three sources are mined, in priority order:

    * **Changed lines** (added *and* removed): a changed signature, a removed
      export, or a renamed definition is exactly the blast-y case we want.
    * **The hunk heading** (git's ``@@ ... @@ def foo(...)`` section text): git
      prints the enclosing definition here when the change is far from it.
    * **Context lines** (unchanged ``  `` lines in the body) that are
      *definitions*: for a small file, the function you edited the body of is a
      context line, not the heading. A git hunk is tight (a few lines of
      context), so a ``def``/``class``/``func`` sitting in it is almost always
      the symbol this change lives inside.

    Only *definition* lines are mined (never arbitrary identifiers on context
    lines), so we measure "this hunk touches the definition/body of NAME", not
    "NAME appears nearby". Stopwords and too-short names are dropped; the result
    is de-duplicated in first-seen order for stable output.
    """
    seen: dict[str, None] = {}

    def _harvest(text: str) -> None:
        for pattern in _DEF_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1)
                if len(name) < _MIN_SYMBOL_LEN:
                    continue
                if name.lower() in _STOPWORDS:
                    continue
                seen.setdefault(name, None)

    # The enclosing definition git prints after the @@ header, when present.
    if hunk.heading.strip():
        _harvest(hunk.heading)

    for raw in hunk.body.split("\n"):
        marker = raw[:1]
        if marker in {"+", "-"}:
            # Any definition on a changed line.
            _harvest(_clean_line(raw))
        elif marker == " ":
            # Only *definitions* on context lines (not every identifier), so we
            # capture the enclosing function/class of a body-only change.
            _harvest_defs_only(_clean_line(raw), seen)
    return list(seen)


# Patterns that specifically start a *definition* (used for context lines, where
# we must be stricter than on changed lines: a bare `NAME =` assignment on an
# unchanged context line is not something this hunk changed, so it's excluded).
_CONTEXT_DEF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b(?:func|function)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"\bexport\s+(?:default\s+)?(?:async\s+)?"
        r"(?:function|class|const|let|var)?\s*([A-Za-z_$][A-Za-z0-9_$]*)"
    ),
)


def _harvest_defs_only(text: str, seen: dict[str, None]) -> None:
    """Harvest only ``def``/``class``/``func`` style definitions from ``text``."""
    for pattern in _CONTEXT_DEF_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1)
            if len(name) < _MIN_SYMBOL_LEN:
                continue
            if name.lower() in _STOPWORDS:
                continue
            seen.setdefault(name, None)


def _git_tracked_files(root: str) -> list[str] | None:
    """Return absolute paths of git-tracked files under ``root`` (or ``None``).

    Uses ``git ls-files`` so the universe honours ``.gitignore`` and excludes
    junk automatically. Returns ``None`` when git is unavailable or ``root`` is
    not a repository, signalling the caller to fall back to a filesystem walk.
    """
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):  # pragma: no cover - defensive
        return None
    if proc.returncode != 0:
        return None
    rels = [p for p in proc.stdout.split("\0") if p]
    return [os.path.join(root, rel) for rel in rels]


def _walk_files(root: str) -> list[str]:
    """Bounded filesystem walk used when git isn't available.

    Skips well-known noise directories and caps the number of files so a giant
    non-git tree can't blow up a run. Only text-ish source extensions are kept.
    """
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if os.path.splitext(fname)[1].lower() in _SCAN_EXTENSIONS:
                out.append(os.path.join(dirpath, fname))
                if len(out) >= _MAX_WALK_FILES:
                    return out
    return out


def _is_scannable(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _SCAN_EXTENSIONS


def build_index(root: str | None = None) -> RepoIndex | None:
    """Build a :class:`RepoIndex` rooted at ``root`` (default: cwd).

    Returns ``None`` when there is nothing worth scanning — no readable tree, or
    a tree with zero source files — so the caller can cleanly skip the rule
    (the "gracefully no-op outside a repo" contract). Prefers git's file list
    and falls back to a bounded walk.
    """
    base = os.path.abspath(root or os.getcwd())
    if not os.path.isdir(base):
        return None

    tracked = _git_tracked_files(base)
    if tracked is not None:
        files = [p for p in tracked if _is_scannable(p)]
    else:
        files = _walk_files(base)

    if not files:
        return None
    return RepoIndex(root=base, files=files)


def _points_for(count: int) -> tuple[int, str] | None:
    """Map a reference count to (points, label), or ``None`` if below threshold."""
    for threshold, points, label in _BUCKETS:
        if count >= threshold:
            return points, label
    return None


def make_rule(index: RepoIndex):
    """Build a blast-radius rule bound to ``index``.

    The returned callable matches the standard ``Rule`` shape
    (``(Hunk, File) -> Iterable[Signal]``). For each hunk it extracts changed
    symbols, counts their references across the repo (excluding the hunk's own
    file), and yields at most one signal per symbol — the highest-reach symbols
    first — so a small edit to a widely-used name floats up the reading order.
    """

    def score(hunk: Hunk, file: File) -> Iterator[Signal]:
        symbols = extract_symbols(hunk)
        if not symbols:
            return
        own = os.path.join(index.root, hunk.file_path.replace("\\", "/"))
        found: list[tuple[int, str, int, str]] = []
        for symbol in symbols:
            count = index.count(symbol, exclude=own)
            bucket = _points_for(count)
            if bucket is None:
                continue
            points, label = bucket
            found.append((points, symbol, count, label))
        # Most impactful first, then by name for a deterministic tie-break.
        found.sort(key=lambda t: (-t[0], -t[2], t[1]))
        for points, symbol, count, label in found:
            places = "place" if count == 1 else "places"
            yield Signal(
                rule=RULE,
                points=points,
                reason=(f"blast radius: '{symbol}' {label} ({count} {places} in the repo)"),
            )

    return score


def make_rule_or_none(index: RepoIndex | None):
    """Convenience: :func:`make_rule` when ``index`` is present, else ``None``.

    Lets the CLI express "add the rule only if we actually have something to
    scan" without an ``if`` at the call site.
    """
    if index is None:
        return None
    return make_rule(index)


def append_rule(rules: Iterable, index: RepoIndex | None, *, weight=None) -> list:
    """Return ``rules`` with the blast-radius rule appended when ``index`` exists.

    A tiny helper so the CLI stays declarative: it hands over the config-built
    rule list and the (maybe-``None``) index and gets back the final list to
    score with. ``weight`` is an optional ``(name, rule) -> rule`` wrapper (the
    config's :meth:`~diff_sommelier.config.Config.apply_weight`) so a
    ``[weights]`` entry for ``blast-radius`` tunes this rule like any other.
    """
    out = list(rules)
    rule = make_rule_or_none(index)
    if rule is not None:
        if weight is not None:
            rule = weight(RULE, rule)
        out.append(rule)
    return out
