"""Tests for the opt-in no-tests rule (:mod:`diff_sommelier.no_tests`).

Covers test-file detection across conventions, the diff-level index, the
threshold gate (trivial diffs stay quiet), the coverage match (a related test
touch silences the signal), and the CLI/config wiring.
"""

from __future__ import annotations

import pytest

from diff_sommelier.config import Config
from diff_sommelier.no_tests import (
    DEFAULT_THRESHOLD,
    RULE,
    append_rule,
    build_index,
    is_test_file,
    make_rule,
    make_rule_or_none,
)
from diff_sommelier.parser import parse_diff


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _big_change(path: str, lines: int = 120) -> str:
    """A diff chunk that adds many lines to ``path`` (clears the risk threshold)."""
    body = "".join(f"+line {i}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1,{lines} @@\n"
        f"-old\n{body}"
    )


def _tiny_change(path: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


def _score_hunks(diff_text: str, *, threshold: int = DEFAULT_THRESHOLD):
    """Return the no-tests signals emitted across every hunk in ``diff_text``."""
    diff = parse_diff(diff_text)
    index = build_index(diff)
    base = Config().rules()
    rule = make_rule(index, base_rules=base, threshold=threshold)
    signals = []
    for file in diff.files:
        for hunk in file.hunks:
            signals.extend(rule(hunk, file))
    return signals


# --------------------------------------------------------------------------- #
# Test-file detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path",
    [
        "tests/test_parser.py",
        "test/test_parser.py",
        "parser_test.py",
        "pkg/parser_test.go",
        "src/foo.test.ts",
        "src/foo.spec.js",
        "__tests__/foo.js",
        "TestFoo.java",
    ],
)
def test_detects_test_files(path):
    assert is_test_file(path)


@pytest.mark.parametrize(
    "path",
    ["diff_sommelier/parser.py", "src/app.ts", "main.go", "README.md"],
)
def test_non_test_files_not_flagged_as_tests(path):
    assert not is_test_file(path)


# --------------------------------------------------------------------------- #
# Core behavior
# --------------------------------------------------------------------------- #
def test_risky_change_no_tests_fires():
    signals = _score_hunks(_big_change("diff_sommelier/foo.py"))
    assert len(signals) == 1
    assert signals[0].rule == RULE
    assert signals[0].points > 0
    assert "no test changes" in signals[0].reason


def test_matching_test_touch_silences_signal():
    diff_text = _big_change("diff_sommelier/foo.py") + _tiny_change("tests/test_foo.py")
    signals = _score_hunks(diff_text)
    # Only the source hunk is a candidate; the matching test touch clears it.
    assert signals == []


def test_unrelated_test_touch_is_softer_but_still_fires():
    diff_text = _big_change("diff_sommelier/foo.py") + _tiny_change("tests/test_other.py")
    signals = _score_hunks(diff_text)
    assert len(signals) == 1
    assert "other tests moved" in signals[0].reason


def test_trivial_change_stays_quiet():
    # A one-line change with no danger/size signal is below threshold.
    signals = _score_hunks(_tiny_change("diff_sommelier/foo.py"))
    assert signals == []


def test_hunk_in_test_file_never_flagged():
    signals = _score_hunks(_big_change("tests/test_foo.py"))
    assert signals == []


def test_non_code_file_never_flagged():
    signals = _score_hunks(_big_change("README.md"))
    assert signals == []


def test_threshold_is_configurable():
    # A high threshold suppresses even a big change.
    signals = _score_hunks(_big_change("diff_sommelier/foo.py"), threshold=100)
    assert signals == []


# --------------------------------------------------------------------------- #
# Wiring helpers
# --------------------------------------------------------------------------- #
def test_make_rule_or_none():
    assert make_rule_or_none(None, base_rules=[]) is None
    idx = build_index(parse_diff(_tiny_change("a.py")))
    assert make_rule_or_none(idx, base_rules=[]) is not None


def test_append_rule_adds_when_index_present():
    base = Config().rules()
    diff = parse_diff(_big_change("diff_sommelier/foo.py"))
    idx = build_index(diff)
    out = append_rule(base, idx)
    assert len(out) == len(base) + 1


def test_append_rule_weight_wrapper_applied():
    base = Config().rules()
    idx = build_index(parse_diff(_big_change("diff_sommelier/foo.py")))
    calls = []

    def weight(name, rule):
        calls.append(name)
        return rule

    append_rule(base, idx, weight=weight)
    assert calls == [RULE]


def test_config_accepts_no_tests_weight():
    # 'no-tests' must be a known, weightable rule name.
    from diff_sommelier.config import _KNOWN_RULES

    assert "no-tests" in _KNOWN_RULES
