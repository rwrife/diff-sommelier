"""Tests for the M1 scaffold: package import, version, and the placeholder
stdin file/hunk counter."""

from __future__ import annotations

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
