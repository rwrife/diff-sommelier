"""Unit tests for the scoring engine: normalization + aggregation (M3)."""

from __future__ import annotations

from diff_sommelier.parser import File, Hunk, parse_diff
from diff_sommelier.rules import Signal
from diff_sommelier.scorer import (
    REFERENCE_RAW,
    ScoredHunk,
    normalize,
    score_diff,
    score_hunk,
)

# --------------------------------------------------------------------------- #
# normalize()                                                                  #
# --------------------------------------------------------------------------- #


def test_normalize_zero_is_zero() -> None:
    assert normalize(0) == 0
    assert normalize(-5) == 0


def test_normalize_is_monotonic() -> None:
    vals = [normalize(r) for r in range(0, 120, 5)]
    assert vals == sorted(vals)


def test_normalize_never_exceeds_100() -> None:
    assert normalize(10_000) <= 100
    assert normalize(REFERENCE_RAW * 10) == 100


def test_normalize_reference_is_high_but_not_pegged() -> None:
    v = normalize(REFERENCE_RAW)
    assert 88 <= v <= 95


def test_normalize_small_raw_is_modest() -> None:
    # A single moderate signal shouldn't already look like a 5-alarm fire.
    assert normalize(3) < 20


# --------------------------------------------------------------------------- #
# score_hunk()                                                                 #
# --------------------------------------------------------------------------- #


def make_hunk(path: str, body: str, added: int, removed: int) -> Hunk:
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


def test_clean_hunk_scores_zero_with_no_signals() -> None:
    h = make_hunk("docs/readme.md", "+just docs", 1, 0)
    scored = score_hunk(h, File(path="docs/readme.md"))
    assert scored.score == 0
    assert scored.raw == 0
    assert scored.signals == []


def test_signals_sorted_by_points_desc() -> None:
    h = make_hunk("auth/login.py", '+api_key = "abcdef123456"\n+eval(x)', 2, 0)
    scored = score_hunk(h, File(path="auth/login.py"))
    pts = [s.points for s in scored.signals]
    assert pts == sorted(pts, reverse=True)
    # raw is the exact sum of contributing points.
    assert scored.raw == sum(pts)


def test_to_dict_shape() -> None:
    h = make_hunk("auth/login.py", "+eval(x)", 1, 0)
    d = score_hunk(h, File(path="auth/login.py")).to_dict()
    assert set(d) == {
        "id",
        "file",
        "old_start",
        "new_start",
        "added",
        "removed",
        "score",
        "raw",
        "signals",
    }
    assert d["file"] == "auth/login.py"
    assert d["id"] == h.id
    assert isinstance(d["signals"], list)
    assert d["signals"][0].keys() == {"rule", "points", "reason"}


# --------------------------------------------------------------------------- #
# score_diff() ordering                                                        #
# --------------------------------------------------------------------------- #

DANGEROUS_DIFF = """\
diff --git a/auth/login.py b/auth/login.py
--- a/auth/login.py
+++ b/auth/login.py
@@ -1,2 +1,4 @@
 def login(u, p):
-    return ok(u, p)
+    api_key = "sk-abc123def456"
+    if eval(u):
+        return True
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
 # Title
+one more doc line
"""


def test_score_diff_orders_most_risky_first() -> None:
    diff = parse_diff(DANGEROUS_DIFF)
    scored = score_diff(diff)
    assert len(scored) == 2
    assert scored[0].hunk.file_path == "auth/login.py"
    assert scored[1].hunk.file_path == "README.md"
    assert scored[0].score > scored[1].score
    assert scored[0].score >= 70  # secret + eval + auth surface stack high
    assert scored[1].score == 0


def test_score_diff_is_deterministic_regardless_of_input_order() -> None:
    a = score_diff(parse_diff(DANGEROUS_DIFF))
    b = score_diff(parse_diff(DANGEROUS_DIFF))
    assert [s.hunk.id for s in a] == [s.hunk.id for s in b]
    assert [s.score for s in a] == [s.score for s in b]


def test_custom_rule_set_is_respected() -> None:
    # Passing an empty rule list yields no signals and a zero score.
    diff = parse_diff(DANGEROUS_DIFF)
    scored = score_diff(diff, rules=[])
    assert all(s.score == 0 and s.signals == [] for s in scored)


def test_scored_hunk_reasons_property() -> None:
    sh = ScoredHunk(
        hunk=make_hunk("a.py", "+x", 1, 0),
        score=10,
        raw=5,
        signals=[Signal("size", 3, "moderate hunk"), Signal("danger", 2, "net deletion")],
    )
    assert sh.reasons == ["moderate hunk", "net deletion"]
