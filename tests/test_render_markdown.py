"""Tests for the Markdown "tasting menu" renderer (backlog #5 — GitHub Action).

The Markdown renderer produces the PR-comment view the GitHub Action posts. It
is deterministic (no colour, no terminal probing), so we pin its *shape* — the
hidden update marker, the summary, the reading-order checklist table, the
collapsed skim-safe section, and the optional CI-gate note — while staying
robust to the exact scores the M3 rules assign (we assert structure and tiers,
not magic numbers).
"""

from __future__ import annotations

from diff_sommelier.parser import parse_diff
from diff_sommelier.render.markdown import COMMENT_MARKER, render_markdown
from diff_sommelier.render.tiers import Tier, tier_for
from diff_sommelier.scorer import score_diff

# Same engineered diff the text-renderer tests use: one hunk per tier.
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


def _menu(**kwargs) -> str:
    scored = score_diff(parse_diff(MENU_DIFF))
    return render_markdown(scored, **kwargs)


def test_starts_with_the_hidden_update_marker() -> None:
    # The marker must be the very first line so the Action can find + edit its
    # own comment instead of spamming new ones.
    out = _menu()
    assert out.splitlines()[0] == COMMENT_MARKER
    assert out.count(COMMENT_MARKER) == 1


def test_empty_diff_is_a_friendly_marker_comment() -> None:
    out = render_markdown([])
    assert out.startswith(COMMENT_MARKER)
    assert "Nothing to taste" in out


def test_summary_reports_hunk_file_counts_and_top_risk() -> None:
    out = _menu()
    scored = score_diff(parse_diff(MENU_DIFF))
    assert "3 hunks" in out
    assert "2 files" not in out  # three distinct files here
    assert "3 files" in out
    assert f"top risk **{scored[0].score}**" in out


def test_review_table_lists_gulp_and_sip_in_reading_order() -> None:
    out = _menu()
    scored = score_diff(parse_diff(MENU_DIFF))
    review = [s for s in scored if tier_for(s.score) is not Tier.SAVOR]
    # The two risky hunks appear in the visible table by location.
    for s in review:
        assert f"{s.hunk.file_path}:{s.hunk.new_start}" in out
    # Reading order: the gulp (auth) row comes before the sip (ci) row.
    assert out.index("auth/login.py:1") < out.index(".github/workflows/ci.yml:1")
    # And it carries a Markdown table + unchecked checklist boxes.
    assert "| Tier | Score | Location | Why |" in out
    assert "[ ]" in out


def test_skim_safe_hunks_are_in_a_collapsed_details_block() -> None:
    out = _menu()
    assert "<details>" in out and "</details>" in out
    assert "<summary>" in out and "Skim-safe" in out
    # The savor (README) hunk lives inside the collapsed section, after it.
    details_idx = out.index("<details>")
    assert out.index("README.md:1") > details_idx


def test_why_column_surfaces_rule_reasons_with_points() -> None:
    out = _menu()
    assert "eval/exec" in out
    assert "authentication/session" in out
    assert "(+" in out  # points are shown inline


def test_pipes_in_reasons_are_escaped_so_tables_do_not_break() -> None:
    # A path with a pipe would otherwise split a Markdown table cell.
    diff = "\n".join(
        [
            "diff --git a/weird|name.py b/weird|name.py",
            "--- a/weird|name.py",
            "+++ b/weird|name.py",
            "@@ -1,1 +1,2 @@",
            " x = 1",
            "+import os; os.system('x')",
            "",
        ]
    )
    out = render_markdown(score_diff(parse_diff(diff)))
    # The literal pipe in the path is backslash-escaped in the cell.
    assert "weird\\|name.py" in out


def test_title_override_sets_the_heading() -> None:
    out = _menu(title="My PR #42")
    assert "## My PR #42" in out
    assert "review-order menu" not in out  # default heading is replaced


def test_fail_over_note_warns_when_a_hunk_trips_the_threshold() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    top = scored[0].score
    out = render_markdown(scored, fail_over=top)  # threshold == worst -> trips
    assert "CI gate" in out
    assert "⛔" in out
    assert str(top) in out


def test_fail_over_note_is_reassuring_when_under_threshold() -> None:
    out = _menu(fail_over=101)  # nothing can reach 101
    assert "CI gate" in out
    assert "✅" in out
    assert "under" in out


def test_no_fail_over_means_no_ci_gate_note() -> None:
    out = _menu()
    assert "CI gate" not in out


def test_legend_footer_explains_the_tiers() -> None:
    out = _menu()
    last = out.strip().splitlines()[-1]
    assert "gulp" in last and "sip" in last and "savor" in last
    assert "diff-sommelier" in last


def test_all_savor_diff_says_nothing_rises_above_skim() -> None:
    diff = "\n".join(
        [
            "diff --git a/README.md b/README.md",
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -1,1 +1,2 @@",
            " # Title",
            "+just docs",
            "",
        ]
    )
    out = render_markdown(score_diff(parse_diff(diff)))
    assert "Nothing rises above skim level" in out
    # Still lists the skim-safe hunk in the collapsed block.
    assert "<details>" in out
    assert "README.md:1" in out


def test_hunk_numbering_is_continuous_across_review_and_skim() -> None:
    # The skim table should continue the numbering after the review table so
    # the whole menu reads as one ranked list.
    out = _menu()
    # Two risky hunks (#1, #2) then one skim-safe hunk (#3).
    assert "| 1 |" in out
    assert "| 2 |" in out
    assert "| 3 |" in out
