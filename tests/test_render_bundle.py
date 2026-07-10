"""Tests for the context-budget bundle renderer (issue #25 — AI reviewers).

The bundle renderer packs the highest-risk hunks, most-dangerous-first, into a
token-bounded, paste-ready review prompt for an AI reviewer. It is
deterministic (no colour, no terminal probing, no network/LLM call), so we pin
its *shape* — the preamble, per-hunk ``file:line`` + why + raw body, the budget
cutoff, and the omitted-count trailer — while staying robust to the exact
scores the M3 rules assign (we assert structure and ordering, not magic
numbers).
"""

from __future__ import annotations

import pytest

from diff_sommelier.parser import parse_diff
from diff_sommelier.render.bundle import (
    CHARS_PER_TOKEN,
    ContextBudget,
    ContextBudgetError,
    estimate_tokens,
    parse_context_budget,
    render_bundle,
)
from diff_sommelier.scorer import score_diff

# The same engineered diff the text/markdown renderer tests use: one hunk per
# tier, so ordering (gulp -> sip -> savor) is deterministic.
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


def _scored():
    return score_diff(parse_diff(MENU_DIFF))


def _bundle(spec: str, **kwargs) -> str:
    return render_bundle(_scored(), budget=parse_context_budget(spec), **kwargs)


# ---------------------------------------------------------------------------
# parse_context_budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,tokens",
    [
        ("6000tok", 6000),
        ("6000t", 6000),
        ("6000tokens", 6000),
        ("6000token", 6000),
        ("  8000 TOK ", 8000),
    ],
)
def test_parse_token_forms(spec: str, tokens: int) -> None:
    budget = parse_context_budget(spec)
    assert budget.is_tokens
    assert not budget.is_count
    assert budget.tokens == tokens


@pytest.mark.parametrize(
    "spec,hunks",
    [
        ("8hunks", 8),
        ("8hunk", 8),
        ("12", 12),
        ("  3 HUNKS ", 3),
    ],
)
def test_parse_count_forms(spec: str, hunks: int) -> None:
    budget = parse_context_budget(spec)
    assert budget.is_count
    assert not budget.is_tokens
    assert budget.hunks == hunks


@pytest.mark.parametrize("spec", ["", "   ", "bogus", "6000mb", "-3hunks", "0tok", "0", "5m"])
def test_parse_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ContextBudgetError):
        parse_context_budget(spec)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_is_ceil_chars_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("x" * 4) == 1
    assert estimate_tokens("x" * 5) == 2  # ceil, not floor
    assert estimate_tokens("x" * 400) == 400 // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# render_bundle — structure
# ---------------------------------------------------------------------------


def test_bundle_has_a_preamble_and_ranks_most_risky_first() -> None:
    out = _bundle("8hunks")
    # Preamble instructs an in-order review.
    assert "highest-risk hunks first" in out
    assert "in order" in out
    # The riskiest hunk (auth/login.py, gulp) appears before the CI hunk, which
    # appears before the docs hunk — i.e. the scorer's order is preserved.
    i_auth = out.index("auth/login.py")
    i_ci = out.index(".github/workflows/ci.yml")
    i_readme = out.index("README.md")
    assert i_auth < i_ci < i_readme


def test_bundle_includes_location_why_and_raw_body_per_hunk() -> None:
    out = _bundle("8hunks")
    # file:line location for the top hunk.
    assert "auth/login.py:1" in out
    # The one-line why carries a rule reason.
    assert "why:" in out
    assert "eval/exec" in out
    # The raw hunk body is fenced as a diff and includes the actual changed line.
    assert "```diff" in out
    assert "@@ -1,2 +1,4 @@" in out
    assert '+    API_KEY = "sk-live-abcd1234abcd1234abcd"' in out


def test_bundle_numbers_hunks_in_reading_order() -> None:
    out = _bundle("8hunks")
    # The top (riskiest) hunk is numbered 1, the next 2, etc.
    assert "### 1. auth/login.py:1" in out
    assert "### 2. .github/workflows/ci.yml:1" in out


# ---------------------------------------------------------------------------
# render_bundle — budget cutoff + omitted trailer
# ---------------------------------------------------------------------------


def test_count_budget_stops_at_the_cut_and_reports_omitted() -> None:
    out = _bundle("2hunks")
    # Only the two riskiest hunks are included; the docs hunk is omitted.
    assert "auth/login.py" in out
    assert ".github/workflows/ci.yml" in out
    assert "README.md" not in out
    # The trailer reports exactly one omitted hunk (singular wording).
    assert "1 lower-risk hunk omitted" in out


def test_count_budget_of_one_keeps_only_the_top_hunk() -> None:
    out = _bundle("1hunk")
    assert "auth/login.py" in out
    assert ".github/workflows/ci.yml" not in out
    assert "2 lower-risk hunks omitted" in out


def test_all_hunks_fit_reports_no_omission() -> None:
    out = _bundle("100hunks")
    assert "All 3 hunks fit within the budget" in out
    # No "omitted" trailer when everything fits.
    assert "omitted" not in out


def test_token_budget_always_keeps_the_single_riskiest_hunk() -> None:
    # A tiny token budget can't fit even the first hunk, but we never drop the
    # scariest one — dropping it would defeat the purpose.
    out = _bundle("1tok")
    assert "auth/login.py" in out
    # ...and everything below it is omitted.
    assert ".github/workflows/ci.yml" not in out
    assert "README.md" not in out
    assert "2 lower-risk hunks omitted" in out


def test_token_budget_admits_more_hunks_as_it_grows() -> None:
    tight = _bundle("1tok")
    roomy = _bundle("100000tok")
    # A generous token budget fits strictly more hunks than a starvation one.
    assert tight.count("```diff") == 1
    assert roomy.count("```diff") == 3


# ---------------------------------------------------------------------------
# render_bundle — title + empty
# ---------------------------------------------------------------------------


def test_title_is_folded_into_the_preamble_as_stated_intent() -> None:
    out = _bundle("8hunks", title="Add SSO login")
    assert "Stated intent:" in out
    assert "Add SSO login" in out


def test_no_title_omits_the_stated_intent_line() -> None:
    out = _bundle("8hunks")
    assert "Stated intent:" not in out


def test_empty_diff_is_a_friendly_no_op_bundle() -> None:
    out = render_bundle([], budget=ContextBudget(hunks=8))
    assert "nothing to review" in out.lower()
    # Still no crash, still has the preamble heading.
    assert "highest-risk hunks first" in out


# ---------------------------------------------------------------------------
# render_bundle — determinism + no side effects
# ---------------------------------------------------------------------------


def test_bundle_is_deterministic() -> None:
    assert _bundle("8hunks", title="X") == _bundle("8hunks", title="X")


def test_bundle_makes_no_network_or_llm_call(monkeypatch) -> None:
    # Defensive: rendering must be pure. If any socket is opened, fail loudly.
    import socket

    def _boom(*_a, **_k):
        raise AssertionError("render_bundle must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    out = _bundle("8hunks")
    assert out  # rendered fine, offline
