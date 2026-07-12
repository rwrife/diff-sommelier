"""Unit tests for the control-flow / off-by-one rule (M3)."""

from __future__ import annotations

from diff_sommelier.parser import ChangeType, File, Hunk
from diff_sommelier.rules import control


def hunk_with(body: str, *, added: int = 1, removed: int = 0, path: str = "src/app.py") -> Hunk:
    """Build a hunk from a raw body string (lines should carry +/-/space)."""
    return Hunk(
        file_path=path,
        old_start=1,
        old_lines=max(1, removed),
        new_start=1,
        new_lines=max(1, added),
        heading="",
        body=body,
        added=added,
        removed=removed,
    )


MODIFIED = File(path="src/app.py", change_type=ChangeType.MODIFIED)


def reasons(hunk: Hunk) -> list[str]:
    return [s.reason for s in control.score(hunk, MODIFIED)]


def points_for(hunk: Hunk) -> int:
    return sum(s.points for s in control.score(hunk, MODIFIED))


# --------------------------------------------------------------------------- #
# Nothing to see here                                                          #
# --------------------------------------------------------------------------- #


def test_comment_only_hunk_fires_nothing() -> None:
    h = hunk_with("+# this used to be a range() loop\n+# and an if branch", added=2)
    assert reasons(h) == []


def test_plain_assignment_fires_nothing() -> None:
    h = hunk_with("+x = 1\n+y = 2", added=2)
    assert reasons(h) == []


# --------------------------------------------------------------------------- #
# Comparison-operator flips (off-by-one bait)                                  #
# --------------------------------------------------------------------------- #


def test_lt_to_lte_flip_flagged() -> None:
    h = hunk_with("-if i < n:\n+if i <= n:", added=1, removed=1)
    rs = reasons(h)
    assert any("comparison operator changed" in r for r in rs)
    assert any("off-by-one" in r for r in rs)


def test_no_flip_when_operators_unchanged() -> None:
    h = hunk_with("-if i < n:\n+if i < m:", added=1, removed=1)
    assert not any("comparison operator changed" in r for r in reasons(h))


# --------------------------------------------------------------------------- #
# Error handling                                                               #
# --------------------------------------------------------------------------- #


def test_bare_except_flagged() -> None:
    h = hunk_with("+    except:\n+        pass", added=2)
    assert any("bare or swallowed exception" in r for r in reasons(h))


def test_swallowed_exception_flagged() -> None:
    h = hunk_with("+    except ValueError: pass")
    assert any("bare or swallowed exception" in r for r in reasons(h))


def test_try_except_flow_flagged() -> None:
    h = hunk_with(
        "+    try:\n+        do()\n+    except KeyError as e:\n+        handle(e)", added=4
    )
    assert any("error-handling flow" in r for r in reasons(h))


def test_raise_flagged() -> None:
    assert any("raise" in r for r in reasons(hunk_with("+    raise ValueError(x)")))


# --------------------------------------------------------------------------- #
# Negation flips                                                               #
# --------------------------------------------------------------------------- #


def test_not_removed_flagged() -> None:
    h = hunk_with("-if not ready:\n+if ready:", added=1, removed=1)
    rs = reasons(h)
    assert any("negation removed" in r for r in rs)


def test_not_added_flagged() -> None:
    h = hunk_with("-if ready:\n+if not ready:", added=1, removed=1)
    assert any("negation added" in r for r in reasons(h))


# --------------------------------------------------------------------------- #
# Loops / bounds / conditionals / early exits                                  #
# --------------------------------------------------------------------------- #


def test_range_loop_flagged() -> None:
    h = hunk_with("+    for i in range(len(xs) - 1):")
    rs = reasons(h)
    assert any("loop bound" in r for r in rs)
    assert any("index/bound arithmetic" in r for r in rs)


def test_while_loop_flagged() -> None:
    assert any("loop bound" in r for r in reasons(hunk_with("+    while i < n:")))


def test_conditional_flagged() -> None:
    assert any("conditional" in r for r in reasons(hunk_with("+    if user.is_admin:")))


def test_boolean_logic_flagged() -> None:
    assert any("boolean logic" in r for r in reasons(hunk_with("+    if a and b or c:")))


def test_early_exit_flagged() -> None:
    assert any(
        "early return/continue/break" in r for r in reasons(hunk_with("+        return None"))
    )


# --------------------------------------------------------------------------- #
# Aggregation                                                                  #
# --------------------------------------------------------------------------- #


def test_repeated_pattern_counts_once_with_multiplier() -> None:
    h = hunk_with("+    if a:\n+    if b:\n+    if c:", added=3)
    sigs = [s for s in control.score(h, MODIFIED) if "conditional" in s.reason]
    assert len(sigs) == 1
    assert "(x3)" in sigs[0].reason


def test_signals_carry_control_rule_name() -> None:
    h = hunk_with("+    while i <= len(xs) - 1:")
    assert all(s.rule == "control" for s in control.score(h, MODIFIED))
    assert points_for(h) > 0
