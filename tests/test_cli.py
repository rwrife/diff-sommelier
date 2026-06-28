"""Tests for the CLI: package import, version, the stdin file/hunk counter
(``count_diff``, still backed by the real M2 parser), and the M4 output modes
(the default human tasting menu and ``--json``)."""

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
