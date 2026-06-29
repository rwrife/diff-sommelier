"""Tests for the attention budget + CI gate (M5, :mod:`diff_sommelier.budget`).

Two halves:

* **Budget math** — parsing the ``--budget`` spec (time and count forms), the
  configurable reading-time model, and where the cut line lands. These build
  synthetic :class:`~diff_sommelier.scorer.ScoredHunk` objects so the
  arithmetic is exact and independent of the M3 rules.
* **The CI gate** — :func:`~diff_sommelier.budget.fail_over` returning the
  worst over-threshold score (or ``None``), which the CLI maps to its exit
  code.
"""

from __future__ import annotations

import pytest

from diff_sommelier.budget import (
    DEFAULT_TIME_MODEL,
    Budget,
    BudgetError,
    TimeModel,
    apply_budget,
    estimate_seconds,
    fail_over,
    format_duration,
    parse_budget,
)
from diff_sommelier.parser import Hunk
from diff_sommelier.scorer import ScoredHunk


def _hunk(changed: int, *, path: str = "f.py", line: int = 1) -> Hunk:
    """A synthetic hunk with a known changed-line count (added split out)."""
    added = changed
    return Hunk(
        file_path=path,
        old_start=line,
        old_lines=0,
        new_start=line,
        new_lines=added,
        heading="",
        body="+x\n" * added,
        added=added,
        removed=0,
    )


def _scored(score: int, changed: int, *, line: int = 1) -> ScoredHunk:
    return ScoredHunk(hunk=_hunk(changed, line=line), score=score, raw=score, signals=[])


# ---------------------------------------------------------------------------
# parse_budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "seconds"),
    [
        ("5m", 300.0),
        ("90s", 90.0),
        ("1m30s", 90.0),
        ("1h", 3600.0),
        ("1h30m", 5400.0),
        ("  10m  ", 600.0),
        ("2M", 120.0),  # case-insensitive
    ],
)
def test_parse_budget_time_forms(spec: str, seconds: float) -> None:
    b = parse_budget(spec)
    assert b.is_time
    assert not b.is_count
    assert b.seconds == pytest.approx(seconds)


@pytest.mark.parametrize(
    ("spec", "hunks"),
    [
        ("10hunks", 10),
        ("1hunk", 1),
        ("12", 12),  # bare integer -> hunk count
        ("  7 hunks ", 7),
    ],
)
def test_parse_budget_count_forms(spec: str, hunks: int) -> None:
    b = parse_budget(spec)
    assert b.is_count
    assert not b.is_time
    assert b.hunks == hunks


@pytest.mark.parametrize("spec", ["", "   ", "bananas", "5x", "0", "0m", "-3", "0hunks", "m"])
def test_parse_budget_rejects_garbage(spec: str) -> None:
    with pytest.raises(BudgetError):
        parse_budget(spec)


# ---------------------------------------------------------------------------
# TimeModel / estimate_seconds
# ---------------------------------------------------------------------------


def test_time_model_default_formula() -> None:
    model = TimeModel()  # 8s overhead + 1.5s/line
    assert model.hunk_seconds(_scored(0, 0)) == pytest.approx(8.0)
    assert model.hunk_seconds(_scored(0, 4)) == pytest.approx(8.0 + 6.0)


def test_time_model_is_configurable() -> None:
    model = TimeModel(seconds_per_changed_line=2.0, per_hunk_overhead_s=10.0)
    assert model.hunk_seconds(_scored(0, 5)) == pytest.approx(10.0 + 10.0)


def test_time_model_rejects_negative_constants() -> None:
    with pytest.raises(ValueError):
        TimeModel(seconds_per_changed_line=-1)
    with pytest.raises(ValueError):
        TimeModel(per_hunk_overhead_s=-1)


def test_estimate_seconds_sums_hunks() -> None:
    rows = [_scored(90, 4), _scored(10, 0)]  # (8+6) + (8+0) = 22
    assert estimate_seconds(rows) == pytest.approx(22.0)
    assert estimate_seconds(rows, DEFAULT_TIME_MODEL) == pytest.approx(22.0)


# ---------------------------------------------------------------------------
# apply_budget — count
# ---------------------------------------------------------------------------


def test_count_budget_cuts_at_n() -> None:
    rows = [_scored(90, 1), _scored(50, 1), _scored(10, 1)]
    result = apply_budget(rows, Budget(hunks=2))
    assert result.cut == 2
    assert result.reviewed == 2
    assert result.skimmed == 1
    assert result.total == 3


def test_count_budget_larger_than_diff_keeps_all() -> None:
    rows = [_scored(90, 1), _scored(10, 1)]
    result = apply_budget(rows, Budget(hunks=99))
    assert result.cut == 2
    assert result.skimmed == 0


# ---------------------------------------------------------------------------
# apply_budget — time
# ---------------------------------------------------------------------------


def test_time_budget_includes_hunks_until_full() -> None:
    # Default model: each 0-changed hunk costs 8s. A 25s budget fits 3 (24s),
    # not 4 (32s).
    rows = [_scored(s, 0) for s in (90, 80, 70, 60, 50)]
    result = apply_budget(rows, Budget(seconds=25.0))
    assert result.cut == 3
    assert result.spent_seconds == pytest.approx(24.0)
    assert result.skimmed == 2


def test_time_budget_always_keeps_top_hunk_even_if_oversized() -> None:
    # The single most-risky hunk costs 8 + 100*1.5 = 158s, far over a 10s
    # budget, but we never skip the scariest hunk.
    rows = [_scored(95, 100), _scored(20, 0)]
    result = apply_budget(rows, Budget(seconds=10.0))
    assert result.cut == 1
    assert result.spent_seconds == pytest.approx(158.0)  # honest overshoot
    assert result.skimmed == 1


def test_time_budget_respects_custom_model() -> None:
    rows = [_scored(s, 0) for s in (90, 80, 70)]
    # 0-changed hunks cost only the 5s overhead -> 12s fits 2, not 3.
    model = TimeModel(seconds_per_changed_line=1.0, per_hunk_overhead_s=5.0)
    result = apply_budget(rows, Budget(seconds=12.0), model)
    assert result.cut == 2


def test_budget_result_reports_total_seconds() -> None:
    rows = [_scored(90, 4), _scored(10, 0)]  # 14 + 8 = 22
    result = apply_budget(rows, Budget(hunks=1))
    assert result.total_seconds == pytest.approx(22.0)


def test_apply_budget_empty_diff() -> None:
    result = apply_budget([], Budget(seconds=300.0))
    assert result.cut == 0
    assert result.total == 0
    assert result.skimmed == 0


# ---------------------------------------------------------------------------
# fail_over
# ---------------------------------------------------------------------------


def test_fail_over_returns_worst_over_threshold() -> None:
    rows = [_scored(92, 1), _scored(70, 1), _scored(40, 1)]
    assert fail_over(rows, 60) == 92
    assert fail_over(rows, 95) is None
    assert fail_over(rows, 40) == 92  # worst, not first-over


def test_fail_over_is_inclusive_of_threshold() -> None:
    rows = [_scored(60, 1)]
    assert fail_over(rows, 60) == 60
    assert fail_over(rows, 61) is None


def test_fail_over_empty_is_none() -> None:
    assert fail_over([], 0) is None


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0s"),
        (45, "45s"),
        (59, "59s"),
        (60, "1m"),
        (90, "1m30s"),
        (125, "2m05s"),
        (3600, "1h"),
        (3660, "1h01m"),
        (5400, "1h30m"),
    ],
)
def test_format_duration(seconds: int, text: str) -> None:
    assert format_duration(seconds) == text
