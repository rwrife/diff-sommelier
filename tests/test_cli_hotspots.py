"""End-to-end tests for --hotspots (issue #9).

Runs the CLI with cwd set to a throwaway git repo where one file has a long,
fix-heavy history, and asserts that a *tiny* change to that file gets a hotspot
signal that lifts it up the reading order. Also covers the graceful no-op
contract (outside a git repo) and that a [weights] entry tunes the rule.
Git-backed cases skip when git is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def _seed_bug_prone(tmp_path: Path, fixes: int = 6) -> None:
    """A repo where ``app/core.py`` is repeatedly changed and fixed."""
    _repo(tmp_path)
    core = tmp_path / "app" / "core.py"
    core.parent.mkdir(parents=True)
    core.write_text("def run():\n    return 0\n")
    (tmp_path / "README.md").write_text("# project\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    for i in range(1, fixes + 1):
        core.write_text(f"def run():\n    return {i}\n")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-qm", f"fix bug {i} in core")


# --------------------------------------------------------------------------- #
# Headline behaviour: a tiny change to a bug-prone file gets lifted.
# --------------------------------------------------------------------------- #


@requires_git
def test_hotspots_lifts_small_change_in_bug_prone_file(tmp_path: Path) -> None:
    _seed_bug_prone(tmp_path, fixes=6)
    # A one-line body edit to core -- tiny by size, but the file's history is bad.
    (tmp_path / "app" / "core.py").write_text("def run():\n    return 99\n")
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-config", "--hotspots", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    assert len(hunks) == 1
    rules = {r["rule"] for r in hunks[0]["signals"]}
    assert "hotspots" in rules
    # The hotspot signal should push it above the "skim-safe" floor.
    assert hunks[0]["score"] >= 25


@requires_git
def test_without_flag_no_hotspot_signal(tmp_path: Path) -> None:
    _seed_bug_prone(tmp_path, fixes=6)
    (tmp_path / "app" / "core.py").write_text("def run():\n    return 99\n")
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-config", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "hotspots" not in rules


@requires_git
def test_hotspots_human_output_mentions_hotspot(tmp_path: Path) -> None:
    _seed_bug_prone(tmp_path, fixes=6)
    (tmp_path / "app" / "core.py").write_text("def run():\n    return 99\n")
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-config", "--no-color", "--hotspots", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "hotspot" in res.stdout
    # Fix-heavy history should surface the "repeatedly fixed" note.
    assert "repeatedly fixed" in res.stdout


# --------------------------------------------------------------------------- #
# Graceful no-op contract.
# --------------------------------------------------------------------------- #


def test_hotspots_flag_is_harmless_outside_repo(tmp_path: Path) -> None:
    # Run with cwd in an *empty, non-repo* dir so build_index() returns None --
    # the flag must not error, just add no signals. Deterministic anywhere.
    empty = tmp_path / "empty"
    empty.mkdir()
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def widget():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    res = _run_cli("--hotspots", "--json", stdin=diff, cwd=empty)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "hotspots" not in rules


@requires_git
def test_hotspots_and_blast_radius_compose(tmp_path: Path) -> None:
    # Both opt-in flags together must not conflict; a bug-prone file still
    # produces a hotspots signal (blast-radius may or may not fire here).
    _seed_bug_prone(tmp_path, fixes=6)
    (tmp_path / "app" / "core.py").write_text("def run():\n    return 99\n")
    _git(tmp_path, "add", "-A")

    res = _run_cli(
        "--staged", "--no-config", "--hotspots", "--blast-radius", "--json", cwd=tmp_path
    )
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "hotspots" in rules


# --------------------------------------------------------------------------- #
# Config weighting applies to the opt-in rule too.
# --------------------------------------------------------------------------- #


@requires_git
def test_weights_mute_hotspots(tmp_path: Path) -> None:
    _seed_bug_prone(tmp_path, fixes=6)
    (tmp_path / "app" / "core.py").write_text("def run():\n    return 99\n")
    # Mute the hotspots rule entirely via .sommelier.toml. A zero weight rounds
    # every hotspot signal to 0 points, which run_rules then drops.
    (tmp_path / ".sommelier.toml").write_text('[weights]\n"hotspots" = 0\n')
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--hotspots", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    rules = {r["rule"] for h in hunks for r in h["signals"]}
    assert "hotspots" not in rules


@requires_git
def test_weights_amplify_hotspots(tmp_path: Path) -> None:
    _seed_bug_prone(tmp_path, fixes=6)
    (tmp_path / "app" / "core.py").write_text("def run():\n    return 99\n")
    (tmp_path / ".sommelier.toml").write_text('[weights]\n"hotspots" = 3.0\n')
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--hotspots", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    hunks = json.loads(res.stdout)
    hotspot_sigs = [r for h in hunks for r in h["signals"] if r["rule"] == "hotspots"]
    assert hotspot_sigs
    # Amplified points should exceed the un-weighted base bucket (>= 14).
    assert max(s["points"] for s in hotspot_sigs) > 14
