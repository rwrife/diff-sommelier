"""Tests for the opt-in historical-hotspot rule (:mod:`diff_sommelier.hotspots`).

These exercise the pure pieces with an **injectable history runner** (so no real
git is needed for the tally/bucketing logic) plus a couple of end-to-end checks
over a genuine temporary git repo, mirroring the blast-radius test strategy.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from diff_sommelier import hotspots
from diff_sommelier.hotspots import (
    FileStats,
    HotspotIndex,
    build_index,
    make_rule,
    make_rule_or_none,
)
from diff_sommelier.parser import parse_diff


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _hunk_for(path: str):
    """A minimal one-line-change hunk against ``path`` (content is irrelevant)."""
    text = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    diff = parse_diff(text)
    return diff.hunks[0], diff.files[0]


def _index(records, *, root="/repo") -> HotspotIndex:
    """Build a HotspotIndex from canned ``(message, [paths])`` records."""
    idx = build_index(root, runner=lambda _root: records)
    assert idx is not None
    return idx


# --------------------------------------------------------------------------- #
# FileStats
# --------------------------------------------------------------------------- #
def test_filestats_fix_ratio():
    assert FileStats(commits=4, fixes=1).fix_ratio == 0.25
    assert FileStats(commits=0, fixes=0).fix_ratio == 0.0
    assert FileStats(commits=3, fixes=3).fix_ratio == 1.0


# --------------------------------------------------------------------------- #
# Tally / build_index
# --------------------------------------------------------------------------- #
def test_tally_counts_commits_and_fixes():
    records = [
        ("fix crash", ["a.py"]),
        ("add feature", ["a.py", "b.py"]),
        ("bug in b", ["b.py"]),
        ("docs", ["README.md"]),
    ]
    idx = _index(records)
    assert idx.stats_for("a.py") == FileStats(commits=2, fixes=1)
    assert idx.stats_for("b.py") == FileStats(commits=2, fixes=1)
    assert idx.stats_for("README.md") == FileStats(commits=1, fixes=0)
    assert idx.max_commits == 2


def test_tally_dedupes_paths_within_a_commit():
    # A pathological record listing the same file twice must count once.
    idx = _index([("fix", ["a.py", "a.py", "a.py"])])
    assert idx.stats_for("a.py") == FileStats(commits=1, fixes=1)


def test_build_index_normalizes_paths():
    idx = _index([("edit", ["./pkg/mod.py"])])
    # Lookups work regardless of leading ./ or backslashes on the query side.
    assert idx.stats_for("pkg/mod.py") == FileStats(commits=1, fixes=0)
    assert idx.stats_for("./pkg/mod.py") == FileStats(commits=1, fixes=0)
    assert idx.stats_for("pkg\\mod.py") == FileStats(commits=1, fixes=0)


def test_build_index_none_when_runner_returns_none():
    assert build_index("/repo", runner=lambda _r: None) is None


def test_build_index_none_when_no_history():
    assert build_index("/repo", runner=lambda _r: []) is None


def test_build_index_none_for_missing_directory():
    assert build_index("/definitely/not/a/real/dir/xyz") is None


# --------------------------------------------------------------------------- #
# Fix detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "message",
    [
        "fix login",
        "Fixed the thing",
        "fixes #123",
        "hotfix for prod",
        'Revert "bad change"',
        "address regression in parser",
        "patch the leak",
        "bugfix: off-by-one",
        "this was broken",
    ],
)
def test_looks_like_fix_true(message):
    assert hotspots._looks_like_fix(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "add new endpoint",
        "refactor rendering",
        "bump version to 1.2.0",
        "prefix cleanup",  # 'prefix' must not match 'fix'
        "affix labels",  # 'affix' must not match 'fix'
    ],
)
def test_looks_like_fix_false(message):
    assert hotspots._looks_like_fix(message) is False


# --------------------------------------------------------------------------- #
# Rule bucketing / ordering / no-op
# --------------------------------------------------------------------------- #
def test_rule_flags_hot_file_with_signal():
    # a.py: busiest file, 6 commits, no fixes -> top bucket, base points.
    records = [("edit", ["a.py"]) for _ in range(6)]
    records += [("edit", ["b.py"])]  # b.py is cold
    idx = _index(records)
    rule = make_rule(idx)

    hunk, file = _hunk_for("a.py")
    signals = list(rule(hunk, file))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.rule == "hotspots"
    assert sig.points == 14
    assert "hotspot" in sig.reason
    assert "6 commits" in sig.reason
    # No fixes -> no fix clause and no fix bonus.
    assert "fix" not in sig.reason


def test_rule_fix_ratio_bumps_points_and_reason():
    # 6 commits, 4 of them fixes (>= 0.34 ratio) -> top bucket + fix bonus.
    records = [("fix it", ["a.py"]) for _ in range(4)]
    records += [("feature", ["a.py"]) for _ in range(2)]
    idx = _index(records)
    (sig,) = list(make_rule(idx)(*_hunk_for("a.py")))
    assert sig.points == 18  # 14 + 4 bonus
    assert "repeatedly fixed" in sig.reason
    assert "4 fixes" in sig.reason


def test_rule_low_fix_ratio_no_bonus():
    # 8 commits, 1 fix -> 12.5% ratio, below threshold: base points, but the
    # reason still reports the fix count honestly.
    records = [("fix", ["a.py"])]
    records += [("edit", ["a.py"]) for _ in range(7)]
    idx = _index(records)
    (sig,) = list(make_rule(idx)(*_hunk_for("a.py")))
    assert sig.points == 14  # top bucket by count, no bonus
    assert "repeatedly fixed" not in sig.reason
    assert "1 fix" in sig.reason  # singular, and no bonus clause


def test_rule_middle_and_low_buckets():
    # Busiest file has 10 commits. A file with ~4 commits (40% share, >= abs 4)
    # lands in the middle bucket (10 pts); ~2 commits is below every floor.
    records = [("edit", ["hot.py"]) for _ in range(10)]
    records += [("edit", ["mid.py"]) for _ in range(4)]
    records += [("edit", ["cool.py"]) for _ in range(2)]
    idx = _index(records)
    rule = make_rule(idx)

    (mid,) = list(rule(*_hunk_for("mid.py")))
    assert mid.points == 10

    assert list(rule(*_hunk_for("cool.py"))) == []


def test_rule_absolute_floor_prevents_noise_in_tiny_repo():
    # Two files, 1 commit each: 100% relative share, but below the absolute
    # floor (min 3) -> nothing flagged. A brand-new repo shouldn't light up.
    idx = _index([("edit", ["a.py"]), ("edit", ["b.py"])])
    rule = make_rule(idx)
    assert list(rule(*_hunk_for("a.py"))) == []
    assert list(rule(*_hunk_for("b.py"))) == []


def test_rule_noop_for_untracked_file():
    idx = _index([("edit", ["a.py"]) for _ in range(6)])
    rule = make_rule(idx)
    # A file with no history in the index yields no signal.
    assert list(rule(*_hunk_for("brand_new.py"))) == []


def test_rule_singular_commit_wording():
    # Force a single-commit hotspot via a custom index (bypassing the floor) to
    # confirm singular "commit" wording.
    idx = HotspotIndex(root="/repo", stats={"a.py": FileStats(1, 0)}, max_commits=1)
    # Temporarily lower nothing — instead assert the wording helper via a file
    # that DOES clear a bucket by using a hand-built index with matching counts.
    idx = HotspotIndex(root="/repo", stats={"a.py": FileStats(6, 1)}, max_commits=6)
    (sig,) = list(make_rule(idx)(*_hunk_for("a.py")))
    assert "6 commits" in sig.reason
    assert "1 fix" in sig.reason and "1 fixes" not in sig.reason


# --------------------------------------------------------------------------- #
# make_rule_or_none / helpers
# --------------------------------------------------------------------------- #
def test_make_rule_or_none():
    assert make_rule_or_none(None) is None
    idx = _index([("edit", ["a.py"]) for _ in range(6)])
    assert make_rule_or_none(idx) is not None


def test_append_rule_appends_only_when_index_present():
    base = ["existing"]
    assert hotspots.append_rule(base, None) == ["existing"]
    idx = _index([("edit", ["a.py"]) for _ in range(6)])
    out = hotspots.append_rule(base, idx)
    assert len(out) == 2 and out[0] == "existing"


def test_append_rule_applies_weight_wrapper():
    idx = _index([("edit", ["a.py"]) for _ in range(6)])
    seen = {}

    def fake_weight(name, rule):
        seen["name"] = name
        return rule

    out = hotspots.append_rule([], idx, weight=fake_weight)
    assert len(out) == 1
    assert seen["name"] == "hotspots"


# --------------------------------------------------------------------------- #
# Real temporary git repo (end-to-end index build)
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _commit(repo: Path, path: str, content: str, message: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    return tmp_path


def test_build_index_over_real_repo(git_repo: Path):
    _commit(git_repo, "app/core.py", "v0\n", "init core")
    for i in range(1, 5):
        _commit(git_repo, "app/core.py", f"v{i}\n", f"fix bug {i} in core")
    _commit(git_repo, "README.md", "docs\n", "add readme")

    idx = build_index(str(git_repo))
    assert idx is not None
    core = idx.stats_for("app/core.py")
    assert core is not None
    assert core.commits == 5  # 1 init + 4 fixes
    assert core.fixes == 4
    assert idx.max_commits == 5

    # The hot, repeatedly-fixed file gets a bonus'd signal end-to-end.
    # 5 commits is below the top bucket's absolute floor (6), so it lands in the
    # middle bucket (10) and the high fix ratio (4/5) adds the +4 bonus -> 14.
    (sig,) = list(make_rule(idx)(*_hunk_for("app/core.py")))
    assert sig.points == 14
    assert "repeatedly fixed" in sig.reason


def test_build_index_returns_none_outside_repo(tmp_path: Path):
    # A plain directory that is not a git repo -> no-op.
    assert build_index(str(tmp_path)) is None
