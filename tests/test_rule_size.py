"""Unit tests for the size rule (M3)."""

from __future__ import annotations

from diff_sommelier.parser import ChangeType, File, Hunk
from diff_sommelier.rules import size


def make_hunk(added: int, removed: int) -> Hunk:
    """Build a hunk with the given +/- counts (body content is irrelevant here)."""
    body_lines = ["+x"] * added + ["-y"] * removed
    return Hunk(
        file_path="src/app.py",
        old_start=1,
        old_lines=max(1, removed),
        new_start=1,
        new_lines=max(1, added),
        heading="",
        body="\n".join(body_lines),
        added=added,
        removed=removed,
    )


FILE = File(path="src/app.py", change_type=ChangeType.MODIFIED)


def test_tiny_hunk_scores_nothing() -> None:
    signals = list(size.score(make_hunk(2, 1), FILE))
    assert signals == []


def test_moderate_hunk_low_points() -> None:
    signals = list(size.score(make_hunk(10, 6), FILE))  # 16 changed
    assert len(signals) == 1
    assert signals[0].rule == "size"
    assert signals[0].points == 3
    assert "16 changed lines" in signals[0].reason


def test_buckets_are_monotonic_in_size() -> None:
    points = [
        next(iter(size.score(make_hunk(changed, 0), FILE)), None) for changed in (20, 60, 120, 250)
    ]
    pts = [s.points for s in points]  # type: ignore[union-attr]
    assert pts == [3, 7, 12, 18]
    assert pts == sorted(pts)


def test_only_one_size_signal_per_hunk() -> None:
    signals = list(size.score(make_hunk(300, 0), FILE))
    assert len(signals) == 1
    assert signals[0].points == 18
    assert "very large hunk" in signals[0].reason


def test_counts_use_both_added_and_removed() -> None:
    # 30 added + 30 removed = 60 changed -> "sizeable" bucket (>=40).
    signals = list(size.score(make_hunk(30, 30), FILE))
    assert signals[0].points == 7
    assert "+30/-30" in signals[0].reason
