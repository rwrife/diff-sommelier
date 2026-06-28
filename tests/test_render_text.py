"""Snapshot-style tests for the plain-text "tasting menu" renderer (M4).

The plain renderer is deterministic by design (no colour, no terminal probing),
so we can pin its exact output. These tests lock the *shape* of the menu — the
summary header, the ranked rows with tier/score/bar/why, wrapping behaviour,
and the legend — so accidental layout regressions are caught, while staying
robust to the precise scores the M3 rules assign (we assert structure and
tiers, not magic numbers baked into a giant golden string).
"""

from __future__ import annotations

from diff_sommelier.parser import parse_diff
from diff_sommelier.render.text import BAR_WIDTH, render_text
from diff_sommelier.render.tiers import Tier, tier_for
from diff_sommelier.scorer import score_diff

# A diff engineered to land one hunk in each tier:
#   - auth/login.py: hardcoded secret + eval in auth code  -> GULP
#   - .github/workflows/ci.yml: CI surface touch           -> SIP
#   - README.md: a one-line docs change                    -> SAVR
MENU_DIFF = "\n".join(
    [
        "diff --git a/auth/login.py b/auth/login.py",
        "--- a/auth/login.py",
        "+++ b/auth/login.py",
        "@@ -1,2 +1,4 @@",
        " def login(u, p):",
        "-    return ok(u, p)",
        '+    API_KEY = "sk-live-abcd1234abcd1234abcd"',
        "+    if eval(u):",
        "+        return True",
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml",
        "--- a/.github/workflows/ci.yml",
        "+++ b/.github/workflows/ci.yml",
        "@@ -1,1 +1,2 @@",
        " name: CI",
        "+  run: deploy.sh",
        "diff --git a/README.md b/README.md",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1,1 +1,2 @@",
        " # Title",
        "+a docs line",
        "",
    ]
)


def _menu() -> str:
    scored = score_diff(parse_diff(MENU_DIFF))
    return render_text(scored, width=100)


def test_empty_menu_is_a_friendly_one_liner() -> None:
    out = render_text([], width=100)
    assert "0 hunks" in out
    assert "\n" not in out  # single line


def test_summary_header_counts_hunks_files_and_top_risk() -> None:
    out = _menu()
    first = out.splitlines()[0]
    assert first.startswith("🍷 diff-sommelier —")
    assert "3 hunks across 3 files" in first
    # Top risk is the most-risky (first) hunk's score.
    scored = score_diff(parse_diff(MENU_DIFF))
    assert f"top risk {scored[0].score}" in first


def test_rows_are_ranked_and_cover_all_three_tiers() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    tiers = [tier_for(s.score) for s in scored]
    # The engineered diff hits one of each tier.
    assert set(tiers) == {Tier.GULP, Tier.SIP, Tier.SAVOR}
    # And they come out most-risky-first.
    assert tiers[0] is Tier.GULP
    assert tiers[-1] is Tier.SAVOR


def test_each_hunk_renders_a_row_with_location_and_tier_label() -> None:
    out = _menu()
    # Every file appears with its line, and every tier label shows up.
    assert "auth/login.py:1" in out
    assert ".github/workflows/ci.yml:1" in out
    assert "README.md:1" in out
    for label in ("GULP", "SIP", "SAVR"):
        assert label in out


def test_top_hunk_why_lists_the_rule_reasons_with_points() -> None:
    out = _menu()
    # The gulp row explains itself: danger + surface reasons, each with (+N).
    assert "eval/exec" in out
    assert "authentication/session" in out
    assert "(+" in out  # points are surfaced inline


def test_score_bar_is_fixed_width_and_proportional() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    out = render_text(scored, width=100)
    # Every bar is exactly BAR_WIDTH cells between brackets.
    bar_lines = [ln for ln in out.splitlines() if "[" in ln and "]" in ln]
    assert bar_lines
    for ln in bar_lines:
        inner = ln[ln.index("[") + 1 : ln.index("]")]
        assert len(inner) == BAR_WIDTH
    # The top (gulp) hunk's bar has more fill than the bottom (savor) one.
    top_bar = bar_lines[0]
    bottom_bar = bar_lines[-1]
    assert top_bar.count("#") > bottom_bar.count("#")


def test_savor_hunk_has_no_signal_placeholder() -> None:
    out = _menu()
    assert "skim-safe" in out  # both the savor row and the legend say it


def test_legend_explains_the_tiers() -> None:
    out = _menu()
    last = out.splitlines()[-1]
    assert "GULP" in last and "SIP" in last and "SAVR" in last
    assert "most-risky-first" in last


def test_long_why_wraps_under_a_hanging_indent() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    # A narrow width forces the gulp row's long "why" to wrap.
    out = render_text(scored, width=70)
    lines = out.splitlines()
    # Find the gulp data row (starts the numbered list at index 1).
    row_idx = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("1 "))
    cont = lines[row_idx + 1]
    # Continuation line is indented (leading spaces) and carries wrapped text.
    assert cont.startswith("   ")
    assert cont.strip()


def test_output_has_no_trailing_newline() -> None:
    out = _menu()
    assert not out.endswith("\n")
