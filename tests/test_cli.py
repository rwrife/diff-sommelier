"""Tests for the CLI: package import, version, the stdin file/hunk counter
(``count_diff``, still backed by the real M2 parser), and the output modes (the
default human tasting menu, ``--json``, the ``--markdown`` PR-comment view, and
the ``--sarif`` code-scanning log)."""

from __future__ import annotations

import json
import subprocess
import sys

import diff_sommelier
from diff_sommelier.cli import DiffCounts, count_diff


def test_package_version_is_a_string() -> None:
    assert isinstance(diff_sommelier.__version__, str)
    assert diff_sommelier.__version__


def test_version_flag_via_module() -> None:
    """`python -m diff_sommelier --version` exits 0 and prints the version."""
    result = subprocess.run(
        [sys.executable, "-m", "diff_sommelier", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert diff_sommelier.__version__ in result.stdout


# Built from parts so the whitespace-only context line (a single space) is
# explicit and doesn't show up as trailing whitespace in this source file.
_BLANK_CONTEXT = " "
SAMPLE_GIT_DIFF = "\n".join(
    [
        "diff --git a/foo.py b/foo.py",
        "index 1111111..2222222 100644",
        "--- a/foo.py",
        "+++ b/foo.py",
        "@@ -1,3 +1,4 @@",
        " import os",
        "+import sys",
        _BLANK_CONTEXT,
        ' print("hi")',
        "diff --git a/bar.py b/bar.py",
        "index 3333333..4444444 100644",
        "--- a/bar.py",
        "+++ b/bar.py",
        "@@ -10,2 +10,2 @@ def thing():",
        "-    return 1",
        "+    return 2",
        "@@ -20,1 +20,2 @@ def other():",
        " x = 1",
        "+y = 2",
        "",
    ]
)


def test_count_diff_git_style_two_files_three_hunks() -> None:
    counts = count_diff(SAMPLE_GIT_DIFF.splitlines(keepends=True))
    assert counts == DiffCounts(files=2, hunks=3)


def test_count_diff_empty_input() -> None:
    assert count_diff([]) == DiffCounts(files=0, hunks=0)


PLAIN_DIFF_U = """\
--- old.txt\t2026-01-01
+++ new.txt\t2026-01-02
@@ -1 +1 @@
-old
+new
"""


def test_count_diff_plain_unified_no_git_header() -> None:
    counts = count_diff(PLAIN_DIFF_U.splitlines(keepends=True))
    assert counts == DiffCounts(files=1, hunks=1)


RISKY_DIFF = "\n".join(
    [
        "diff --git a/auth/login.py b/auth/login.py",
        "--- a/auth/login.py",
        "+++ b/auth/login.py",
        "@@ -1,2 +1,3 @@",
        " def login(u, p):",
        "-    return ok(u, p)",
        "+    if eval(u):",
        "+        return True",
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


def test_json_flag_emits_scored_hunks() -> None:
    """`--json` returns a JSON array of scored, explained hunks."""
    result = _run_cli(RISKY_DIFF, "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 1
    hunk = payload[0]
    assert hunk["file"] == "auth/login.py"
    assert hunk["score"] > 0
    assert any(s["rule"] == "danger" for s in hunk["signals"])
    assert any("eval/exec" in s["reason"] for s in hunk["signals"])


def test_default_prints_the_human_tasting_menu() -> None:
    """Without --json the CLI prints the ranked tasting menu (M4).

    stdout here is a pipe (captured), so the renderer auto-selects the
    deterministic plain-text path: no ANSI escapes, but the menu skeleton
    (summary header, ranked row, tier, why, legend) is present.
    """
    result = _run_cli(RISKY_DIFF)
    assert result.returncode == 0
    out = result.stdout
    assert "\x1b[" not in out  # piped -> plain, no colour
    assert "diff-sommelier —" in out
    assert "1 hunk across 1 file" in out
    assert "auth/login.py:1" in out
    assert "GULP" in out
    assert "eval/exec" in out
    assert "most-risky-first" in out


def test_no_color_flag_forces_plain_text() -> None:
    """--no-color yields plain text with no ANSI even if colour were possible."""
    result = _run_cli(RISKY_DIFF, "--no-color")
    assert result.returncode == 0
    assert "\x1b[" not in result.stdout
    assert "diff-sommelier —" in result.stdout


def test_json_empty_input_is_empty_array() -> None:
    result = _run_cli("", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_default_empty_input_is_friendly() -> None:
    """An empty piped diff still prints a (friendly) menu, exit 0."""
    result = _run_cli("")
    assert result.returncode == 0
    assert "0 hunks" in result.stdout


def test_markdown_flag_emits_a_pr_comment_menu() -> None:
    """`--markdown` prints the GitHub-flavoured PR-comment menu (backlog #5)."""
    result = _run_cli(RISKY_DIFF, "--markdown")
    assert result.returncode == 0
    out = result.stdout
    # Hidden update marker leads, then the heading, table, and legend.
    assert out.startswith("<!-- diff-sommelier:review-menu -->")
    assert "| Tier | Score | Location | Why |" in out
    assert "auth/login.py:1" in out
    assert "eval/exec" in out


def test_markdown_title_flag_sets_the_heading() -> None:
    result = _run_cli(RISKY_DIFF, "--markdown", "--title", "My PR #7")
    assert result.returncode == 0
    assert "## My PR #7" in result.stdout


def test_markdown_and_json_are_mutually_exclusive() -> None:
    """Only one output mode may be selected; argparse rejects the combo (exit 2)."""
    result = _run_cli(RISKY_DIFF, "--markdown", "--json")
    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_markdown_with_fail_over_still_prints_menu_but_exits_nonzero() -> None:
    """The CI-gate exit code must not suppress the comment body on stdout.

    The GitHub Action relies on this: it captures stdout (the menu) to post the
    comment, and separately uses the non-zero exit as the failing status check.
    """
    result = _run_cli(RISKY_DIFF, "--markdown", "--fail-over", "1")
    assert result.returncode == 1
    # The full menu is still on stdout for the Action to post.
    assert result.stdout.startswith("<!-- diff-sommelier:review-menu -->")
    assert "CI gate" in result.stdout
    # The trip reason goes to stderr (doesn't pollute the comment body).
    assert "fail-over tripped" in result.stderr


def test_markdown_empty_input_is_a_friendly_marker_comment() -> None:
    result = _run_cli("", "--markdown")
    assert result.returncode == 0
    assert result.stdout.startswith("<!-- diff-sommelier:review-menu -->")
    assert "Nothing to taste" in result.stdout


def test_sarif_flag_emits_a_sarif_log() -> None:
    """`--sarif` prints a SARIF 2.1.0 log (a JSON object) of the ranked hunks."""
    result = _run_cli(RISKY_DIFF, "--sarif")
    assert result.returncode == 0
    log = json.loads(result.stdout)
    assert log["version"] == "2.1.0"
    assert log["$schema"].endswith("sarif-schema-2.1.0.json")
    run = log["runs"][0]
    assert run["tool"]["driver"]["name"] == "diff-sommelier"
    # One result for the single risky hunk, mapped to the gulp -> error level.
    result_obj = run["results"][0]
    assert result_obj["level"] == "error"
    assert result_obj["ruleId"] == "danger"
    loc = result_obj["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "auth/login.py"
    assert loc["region"]["startLine"] == 1
    assert "eval/exec" in result_obj["message"]["text"]
    # The firing rule resolves in the driver catalog.
    assert any(r["id"] == "danger" for r in run["tool"]["driver"]["rules"])


def test_sarif_title_flag_is_recorded_in_run_properties() -> None:
    result = _run_cli(RISKY_DIFF, "--sarif", "--title", "My PR #7")
    assert result.returncode == 0
    log = json.loads(result.stdout)
    assert log["runs"][0]["properties"]["title"] == "My PR #7"


def test_sarif_and_json_are_mutually_exclusive() -> None:
    """Only one output mode may be selected; argparse rejects the combo (exit 2)."""
    result = _run_cli(RISKY_DIFF, "--sarif", "--json")
    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_sarif_and_markdown_are_mutually_exclusive() -> None:
    result = _run_cli(RISKY_DIFF, "--sarif", "--markdown")
    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_sarif_with_fail_over_still_prints_log_but_exits_nonzero() -> None:
    """The CI-gate exit code must not suppress the SARIF body on stdout.

    An uploader step captures stdout (the log) to hand to ``upload-sarif``, and
    separately uses the non-zero exit as the failing status check.
    """
    result = _run_cli(RISKY_DIFF, "--sarif", "--fail-over", "1")
    assert result.returncode == 1
    # The full, valid SARIF log is still on stdout for the uploader.
    log = json.loads(result.stdout)
    assert log["version"] == "2.1.0"
    assert log["runs"][0]["properties"]["failOver"] == 1
    # The trip reason goes to stderr (doesn't corrupt the JSON on stdout).
    assert "fail-over tripped" in result.stderr


def test_sarif_empty_input_is_a_valid_empty_log() -> None:
    result = _run_cli("", "--sarif")
    assert result.returncode == 0
    log = json.loads(result.stdout)
    assert log["version"] == "2.1.0"
    assert log["runs"][0]["results"] == []


# A three-hunk diff (one per tier) so the --context-budget cutoff and the
# omitted-count trailer are exercised end-to-end through the CLI.
CONTEXT_DIFF = "\n".join(
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


def test_context_budget_hunks_emits_a_paste_ready_bundle() -> None:
    """`--context-budget 8hunks` prints a ranked, paste-ready review bundle."""
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "8hunks")
    assert result.returncode == 0
    out = result.stdout
    assert "highest-risk hunks first" in out
    # Ranked most-risky-first: auth hunk before CI hunk before docs hunk.
    assert out.index("auth/login.py") < out.index(".github/workflows/ci.yml")
    # Raw hunk body is included for the reviewer.
    assert "```diff" in out
    assert "All 3 hunks fit within the budget" in out


def test_context_budget_tokens_form_works_over_stdin() -> None:
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "6000tok")
    assert result.returncode == 0
    assert "auth/login.py:1" in result.stdout


def test_context_budget_stops_at_the_cut_and_reports_omitted() -> None:
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "2hunks")
    assert result.returncode == 0
    out = result.stdout
    assert "auth/login.py" in out
    assert ".github/workflows/ci.yml" in out
    assert "README.md" not in out
    assert "1 lower-risk hunk omitted" in out


def test_context_budget_title_is_folded_into_the_preamble() -> None:
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "8hunks", "--title", "Add SSO")
    assert result.returncode == 0
    assert "Stated intent:" in result.stdout
    assert "Add SSO" in result.stdout


def test_context_budget_and_json_are_mutually_exclusive() -> None:
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "8hunks", "--json")
    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_context_budget_and_sarif_are_mutually_exclusive() -> None:
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "8hunks", "--sarif")
    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_context_budget_rejects_a_bad_spec_cleanly() -> None:
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "bogus")
    assert result.returncode == 2
    assert "unrecognized context budget" in result.stderr


def test_context_budget_with_fail_over_prints_bundle_but_exits_nonzero() -> None:
    """The CI-gate exit code must not suppress the bundle on stdout."""
    result = _run_cli(CONTEXT_DIFF, "--context-budget", "8hunks", "--fail-over", "1")
    assert result.returncode == 1
    # The bundle is still on stdout for piping to a reviewer.
    assert "highest-risk hunks first" in result.stdout
    # The trip reason goes to stderr (doesn't corrupt the bundle on stdout).
    assert "fail-over tripped" in result.stderr


def test_context_budget_empty_input_is_a_friendly_no_op() -> None:
    result = _run_cli("", "--context-budget", "8hunks")
    assert result.returncode == 0
    assert "nothing to review" in result.stdout.lower()
