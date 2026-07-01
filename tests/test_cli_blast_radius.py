"""End-to-end tests for --blast-radius (issue #8).

Runs the CLI with cwd set to a throwaway git repo where a *tiny* change touches
a widely-referenced symbol, and asserts the blast-radius signal lifts that hunk.
Also covers the graceful no-op contract (outside a repo / nothing to scan) and
that a [weights] entry tunes the rule. Git-backed cases skip when git is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from diff_sommelier import blast_radius as br

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _run_cli(*args: str, cwd: Path | None = None, stdin: str | None = None):
    return subprocess.run(
        [sys.executable, "-m", "diff_sommelier", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(path: Path) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")


def _seed_widely_used(tmp_path: Path, uses: int = 25) -> None:
    """A helper defined once and referenced across many caller files, committed."""
    _repo(tmp_path)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "util.py").write_text("def compute_total(items):\n    return sum(items)\n")
    callers = tmp_path / "callers"
    callers.mkdir()
    for i in range(uses):
        (callers / f"c{i}.py").write_text(
            "from lib.util import compute_total\nprint(compute_total([1, 2, 3]))\n"
        )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")


# --------------------------------------------------------------------------- #
# The headline behaviour: a tiny hunk touching a widely-used symbol.
# --------------------------------------------------------------------------- #


@requires_git
def test_blast_radius_lifts_small_widely_used_change(tmp_path: Path) -> None:
    _seed_widely_used(tmp_path, uses=25)
    # A one-line body edit to compute_total -- tiny by size, huge by reach.
    (tmp_path / "lib" / "util.py").write_text(
        "def compute_total(items):\n    return sum(items) + 0\n"
    )
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-config", "--blast-radius", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    assert len(hunks) == 1
    rules = {r["rule"] for r in hunks[0]["signals"]}
    assert "blast-radius" in rules
    # The blast signal should push it above the "skim-safe" floor.
    assert hunks[0]["score"] >= 25


@requires_git
def test_without_flag_no_blast_signal(tmp_path: Path) -> None:
    _seed_widely_used(tmp_path, uses=25)
    (tmp_path / "lib" / "util.py").write_text(
        "def compute_total(items):\n    return sum(items) + 0\n"
    )
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-config", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "blast-radius" not in rules


@requires_git
def test_blast_radius_human_output_mentions_symbol(tmp_path: Path) -> None:
    _seed_widely_used(tmp_path, uses=25)
    (tmp_path / "lib" / "util.py").write_text(
        "def compute_total(items):\n    return sum(items) + 0\n"
    )
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-config", "--no-color", "--blast-radius", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "blast radius" in res.stdout
    assert "compute_total" in res.stdout


# --------------------------------------------------------------------------- #
# Graceful no-op contract.
# --------------------------------------------------------------------------- #


def test_build_index_none_outside_repo(tmp_path: Path) -> None:
    # An empty dir with no source files -> nothing to scan -> None.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert br.build_index(str(empty)) is None


def test_build_index_none_for_missing_dir(tmp_path: Path) -> None:
    assert br.build_index(str(tmp_path / "does-not-exist")) is None


def test_blast_radius_flag_is_harmless_on_plain_stdin() -> None:
    # No repo context; the flag must not error, just add no signals.
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def widget():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    res = _run_cli("--blast-radius", "--json", stdin=diff)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "blast-radius" not in rules


# --------------------------------------------------------------------------- #
# Config weighting applies to the opt-in rule too.
# --------------------------------------------------------------------------- #


@requires_git
def test_weights_mute_blast_radius(tmp_path: Path) -> None:
    _seed_widely_used(tmp_path, uses=25)
    (tmp_path / "lib" / "util.py").write_text(
        "def compute_total(items):\n    return sum(items) + 0\n"
    )
    # Mute the blast-radius rule entirely via .sommelier.toml. A zero weight
    # rounds every blast signal to 0 points, which run_rules then drops -- so
    # the rule effectively disappears from the output while the built-ins stay.
    (tmp_path / ".sommelier.toml").write_text('[weights]\n"blast-radius" = 0\n')
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--blast-radius", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "blast-radius" not in rules
