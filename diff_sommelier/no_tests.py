"""No-tests rule (opt-in, ``--no-tests``).

Where the other rules judge a hunk by its own contents or location, this rule
judges a risky code hunk by its **company** — specifically, whether the *same
diff* also touches a plausibly-related test file. A non-trivial change to a
source module that ships with **zero** accompanying test changes deserves extra
reviewer attention: it is exactly the kind of change that quietly alters
behavior with no safety net moving alongside it.

The heuristic is cheap and needs no coverage tooling — just the diff:

1. **Classify every file in the diff as test-or-not.** A file is a "test file"
   if its path matches a common convention: ``test_*.py``, ``*_test.py``, a
   ``tests/`` (or ``test/`` / ``__tests__/``) directory, ``*.test.*``, or
   ``*.spec.*``. This spans Python, JS/TS, Go, and friends.

2. **For each non-test source hunk above a risk threshold**, check whether the
   diff touches any test file that plausibly *relates* to the changed module.
   "Related" = a touched test file whose path/name references the changed
   module's stem (e.g. a change to ``parser.py`` is covered by touching
   ``test_parser.py``, ``parser_test.go``, or a ``tests/…`` file that mentions
   ``parser``). Any test file touch also counts as generic coverage so a diff
   that clearly moved *some* tests isn't nagged on every hunk.

3. **If nothing plausibly-related was touched**, emit a :class:`Signal`
   ("risky change with no test changes in this diff").

The "risk threshold" keeps this quiet on trivial diffs: the rule re-scores the
hunk with the *base* rule pack it is handed and only fires when that raw score
clears :data:`DEFAULT_THRESHOLD` (configurable). This means a one-line typo fix
with no tests stays silent, while a chunky, dangerous, or high-surface hunk with
no tests floats up.

Everything here is **opt-in** (nothing runs unless ``--no-tests`` is passed) and
**local/offline** — it only inspects the diff already in hand. It is weightable
via the ``[weights]`` ``no-tests`` key like any other rule.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from diff_sommelier.parser import Diff, File, Hunk
from diff_sommelier.rules import Rule, Signal, run_rules

__all__ = [
    "RULE",
    "DEFAULT_THRESHOLD",
    "TestFileIndex",
    "is_test_file",
    "build_index",
    "make_rule",
    "make_rule_or_none",
    "append_rule",
]

RULE = "no-tests"

# Raw base-rule points a hunk must reach before "no accompanying test change"
# is worth flagging. Tuned against the scorer's REFERENCE_RAW (45): this is a
# "meaningful, not trivial" bar — roughly a sizeable hunk, or one that tripped a
# surface/danger signal — so trivial diffs never get nagged.
DEFAULT_THRESHOLD = 10

_POINTS = 9

# Path conventions that mark a file as a test file. Matched case-insensitively
# against the (forward-slashed) path. Deliberately broad and cross-language.
_TEST_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)tests?/"),  # tests/ or test/ directory
    re.compile(r"(^|/)__tests__/"),  # JS/TS convention
    re.compile(r"(^|/)test_[^/]+$"),  # test_foo.py
    re.compile(r"_test\.[^/.]+$"),  # foo_test.py / foo_test.go
    re.compile(r"\.test\.[^/.]+$"),  # foo.test.ts
    re.compile(r"\.spec\.[^/.]+$"),  # foo.spec.ts
    re.compile(r"(^|/)test[^/]*\.[^/.]+$"),  # TestFoo.java-ish / test-foo.js
)

# Files that are source-like but shouldn't demand tests (config, docs, data).
# A hunk in one of these never asks for a test change.
_NON_CODE_SUFFIXES = (
    ".md",
    ".rst",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".lock",
    ".csv",
    ".svg",
    ".png",
    ".jpg",
    ".gif",
)


def _norm(path: str) -> str:
    """Normalize a path to forward slashes, lowercased, for matching."""
    return path.replace("\\", "/").lower()


def is_test_file(path: str) -> bool:
    """True when ``path`` looks like a test file by common convention."""
    normalized = _norm(path)
    return any(pat.search(normalized) for pat in _TEST_PATH_PATTERNS)


def _stem(path: str) -> str:
    """The bare module name of ``path``: basename minus a single extension.

    ``a/b/parser.py`` -> ``parser``; ``a/b/Parser.tsx`` -> ``parser``.
    """
    base = _norm(path).rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base


def _looks_like_code(path: str) -> bool:
    """True when ``path`` is a source file we'd expect tests for."""
    normalized = _norm(path)
    return not normalized.endswith(_NON_CODE_SUFFIXES)


@dataclass(frozen=True)
class TestFileIndex:
    """The test-file evidence extracted from a single diff.

    ``any_tests`` is True when the diff touches *any* test file at all (a diff
    that clearly moved some tests gets the benefit of the doubt). ``stems`` is
    the set of module stems referenced by touched test-file paths, used to match
    a changed source module to a plausibly-related test.
    """

    any_tests: bool
    stems: frozenset[str]

    def covers(self, source_path: str) -> bool:
        """True when a touched test plausibly relates to ``source_path``.

        A source module is considered covered when the diff touches *any* test
        file (generic coverage signal) whose path references the module's stem,
        or — as a lenient fallback — when the diff touches any test file at all
        while also naming the stem somewhere in a test path.
        """
        stem = _stem(source_path)
        if stem and stem in self.stems:
            return True
        return False


def _test_stems(path: str) -> Iterator[str]:
    """Yield module stems a test path might be exercising.

    From ``tests/test_parser.py`` -> ``parser`` (strip a ``test_`` prefix); from
    ``parser_test.go`` -> ``parser`` (strip a ``_test`` suffix); plus the raw
    stem itself. Also yields every path segment stem so a ``tests/parser/…``
    layout matches ``parser``.
    """
    normalized = _norm(path)
    for segment in normalized.split("/"):
        if not segment:
            continue
        stem = segment
        if "." in stem:
            stem = stem.rsplit(".", 1)[0]
        # Strip common test affixes to recover the module under test.
        stem = re.sub(r"^test[_-]", "", stem)
        stem = re.sub(r"[_-]test$", "", stem)
        stem = re.sub(r"\.(test|spec)$", "", stem)
        if stem and stem not in ("test", "tests", "__tests__", "spec"):
            yield stem


def build_index(diff: Diff) -> TestFileIndex:
    """Extract the :class:`TestFileIndex` for ``diff``.

    Walks every file in the diff, collecting the stems of touched test files.
    Cheap and pure — it only reads the parsed diff already in hand.
    """
    any_tests = False
    stems: set[str] = set()
    for file in diff.files:
        path = file.path or file.new_path or file.old_path or ""
        if not path:
            continue
        if is_test_file(path):
            any_tests = True
            stems.update(_test_stems(path))
    return TestFileIndex(any_tests=any_tests, stems=frozenset(stems))


def make_rule(
    index: TestFileIndex,
    *,
    base_rules: Iterable[Rule],
    threshold: int = DEFAULT_THRESHOLD,
) -> Rule:
    """Bind a test-file ``index`` into a ``(Hunk, File) -> [Signal]`` rule.

    The returned rule fires at most one signal per hunk, and only when **all**
    of these hold:

    * the hunk lives in a non-test, code-like source file,
    * re-scoring the hunk with ``base_rules`` clears ``threshold`` (so trivial
      diffs stay quiet), and
    * the diff touches no plausibly-related test file for that module.
    """
    base = list(base_rules)

    def score(hunk: Hunk, file: File) -> Iterator[Signal]:
        path = file.path or hunk.file_path or ""
        if not path or is_test_file(path) or not _looks_like_code(path):
            return
        # Only nag on hunks that are already meaningfully risky on their own.
        base_signals = run_rules(hunk, file, base)
        raw = sum(s.points for s in base_signals)
        if raw < threshold:
            return
        if index.covers(path):
            return
        if index.any_tests:
            # Some tests moved, just not obviously for this module. Softer nag.
            yield Signal(
                rule=RULE,
                points=max(1, _POINTS - 3),
                reason=(
                    f"risky change to {_stem(path)} with no matching test change "
                    "in this diff (other tests moved)"
                ),
            )
            return
        yield Signal(
            rule=RULE,
            points=_POINTS,
            reason="risky change with no test changes anywhere in this diff",
        )

    return score


def make_rule_or_none(
    index: TestFileIndex | None,
    *,
    base_rules: Iterable[Rule],
    threshold: int = DEFAULT_THRESHOLD,
) -> Rule | None:
    """:func:`make_rule` when ``index`` is present, else ``None``."""
    if index is None:
        return None
    return make_rule(index, base_rules=base_rules, threshold=threshold)


def append_rule(
    rules: Iterable[Rule],
    index: TestFileIndex | None,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    weight: Callable[[str, Rule], Rule] | None = None,
) -> list[Rule]:
    """Return ``rules`` with the no-tests rule appended when ``index`` exists.

    Mirrors the other opt-in rules' ``append_rule`` helpers so the CLI stays
    declarative. The base rules the no-tests rule re-scores against are the
    ``rules`` passed in (the config-tuned built-ins), so weighting/config flow
    consistently. ``weight`` is the config's ``apply_weight`` wrapper so a
    ``[weights]`` ``no-tests`` entry tunes this rule like any other.
    """
    out = list(rules)
    rule = make_rule_or_none(index, base_rules=out, threshold=threshold)
    if rule is not None:
        if weight is not None:
            rule = weight(RULE, rule)
        out.append(rule)
    return out
