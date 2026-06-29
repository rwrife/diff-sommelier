"""CLI + renderer tests for the M5 attention budget and CI gate.

Covers the end-to-end behaviour a user/CI sees:

* ``--budget`` draws a visible cut line in the plain-text menu (review above,
  skim below) and is ignored under ``--json``;
* an invalid ``--budget`` exits 2 with a clear message;
* ``--fail-over`` returns a non-zero exit only when a hunk meets/exceeds the
  threshold, and works alongside ``--json``;
* the plain-text renderer inserts the cut row at the right index.
"""

from __future__ import annotations

import json
import subprocess
import sys

from diff_sommelier.budget import apply_budget, parse_budget
from diff_sommelier.cli import EXIT_FAIL_OVER
from diff_sommelier.parser import parse_diff
from diff_sommelier.render.text import render_text
from diff_sommelier.scorer import score_diff

# Three hunks landing in distinct tiers and risk order:
#   1. auth/login.py  — hardcoded secret + eval in auth code  -> GULP (top)
#   2. db/migrate.py  — raw SQL                                -> SIP
#   3. README.md      — a docs line                            -> SAVR
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
        "diff --git a/db/migrate.py b/db/migrate.py",
        "--- a/db/migrate.py",
        "+++ b/db/migrate.py",
        "@@ -10,1 +10,3 @@ def run():",
        "-    pass",
        '+    cursor.execute("DROP TABLE users")',
        '+    cursor.execute("UPDATE users SET admin=1")',
        "diff --git a/README.md b/README.md",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1,1 +1,2 @@",
        " # Title",
        "+a docs line",
        "",
    ]
)


def _run_cli(stdin: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "diff_sommelier", *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# --budget rendering
# ---------------------------------------------------------------------------


def test_budget_count_draws_cut_line() -> None:
    result = _run_cli(MENU_DIFF, "--budget", "2hunks", "--no-color")
    assert result.returncode == 0
    out = result.stdout
    assert "cut:" in out
    assert "review 2 above" in out
    assert "skim 1 below" in out
    # The cut row sits between hunk 2 and hunk 3.
    lines = out.splitlines()
    cut_idx = next(i for i, line_ in enumerate(lines) if "cut:" in line_)
    before = "\n".join(lines[:cut_idx])
    after = "\n".join(lines[cut_idx:])
    assert "db/migrate.py" in before
    assert "README.md" in after


def test_budget_time_shows_estimate() -> None:
    # A tight 20s budget fits only the top hunk; the cut line reports the spend.
    result = _run_cli(MENU_DIFF, "--budget", "20s", "--no-color")
    assert result.returncode == 0
    assert "cut:" in result.stdout
    assert "review 1 above" in result.stdout
    assert "budget 20s" in result.stdout


def test_generous_budget_has_no_cut_line() -> None:
    # Everything fits, so there's nothing to skim and no cut row is drawn.
    result = _run_cli(MENU_DIFF, "--budget", "10hunks", "--no-color")
    assert result.returncode == 0
    assert "cut:" not in result.stdout


def test_budget_ignored_with_json() -> None:
    result = _run_cli(MENU_DIFF, "--budget", "2hunks", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)  # still valid JSON, no cut artefacts
    assert len(payload) == 3


def test_invalid_budget_exits_usage_error() -> None:
    result = _run_cli(MENU_DIFF, "--budget", "bananas")
    assert result.returncode == 2
    assert "unrecognized budget" in result.stderr


# ---------------------------------------------------------------------------
# --fail-over exit codes
# ---------------------------------------------------------------------------


def test_fail_over_trips_nonzero_exit() -> None:
    result = _run_cli(MENU_DIFF, "--fail-over", "60", "--no-color")
    assert result.returncode == EXIT_FAIL_OVER
    assert "fail-over tripped" in result.stderr
    # The menu still prints on stdout even when the gate trips.
    assert "diff-sommelier —" in result.stdout


def test_fail_over_passes_when_below_threshold() -> None:
    result = _run_cli(MENU_DIFF, "--fail-over", "99", "--no-color")
    assert result.returncode == 0
    assert "fail-over" not in result.stderr


def test_fail_over_works_with_json() -> None:
    result = _run_cli(MENU_DIFF, "--fail-over", "60", "--json")
    assert result.returncode == EXIT_FAIL_OVER
    payload = json.loads(result.stdout)
    assert any(h["score"] >= 60 for h in payload)


def test_fail_over_and_budget_together() -> None:
    result = _run_cli(MENU_DIFF, "--budget", "2hunks", "--fail-over", "60", "--no-color")
    assert result.returncode == EXIT_FAIL_OVER
    assert "cut:" in result.stdout
    assert "fail-over tripped" in result.stderr


# ---------------------------------------------------------------------------
# renderer-level cut row placement
# ---------------------------------------------------------------------------


def test_render_text_inserts_cut_at_index() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    budget = apply_budget(scored, parse_budget("1hunk"))
    out = render_text(scored, width=100, budget=budget)
    lines = out.splitlines()
    cut_lines = [i for i, line_ in enumerate(lines) if "cut:" in line_]
    assert len(cut_lines) == 1
    # Exactly one hunk (row "1") precedes the cut.
    head = "\n".join(lines[: cut_lines[0]])
    assert "auth/login.py" in head
    assert "db/migrate.py" not in head


def test_render_text_no_cut_when_budget_covers_all() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    budget = apply_budget(scored, parse_budget("50hunks"))
    out = render_text(scored, width=100, budget=budget)
    assert "cut:" not in out


def test_render_text_without_budget_is_unchanged() -> None:
    scored = score_diff(parse_diff(MENU_DIFF))
    out = render_text(scored, width=100)
    assert "cut:" not in out
