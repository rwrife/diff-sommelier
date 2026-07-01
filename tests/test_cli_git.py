"""CLI integration tests for the M6 git/config ergonomics: --staged, --range,
--config, and --no-config. Git-backed cases run the CLI with cwd set to a
throwaway repo; they skip cleanly when git is unavailable."""

from __future__ import annotations

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


@requires_git
def test_staged_menu_over_temp_repo(tmp_path: Path) -> None:
    _repo(tmp_path)
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "login.py").write_text("def login(u, p):\n    return True\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (auth / "login.py").write_text("def login(u, p):\n    if eval(u):\n        return True\n")
    _git(tmp_path, "add", "-A")

    res = _run_cli("--staged", "--no-color", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "auth/login.py:1" in res.stdout
    assert "GULP" in res.stdout
    assert "eval/exec" in res.stdout


@requires_git
def test_range_json_over_temp_repo(tmp_path: Path) -> None:
    import json

    _repo(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("a = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "first")
    f.write_text("a = 2\nb = 3\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "second")

    res = _run_cli("--range", "HEAD~1..HEAD", "--json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload and payload[0]["file"] == "mod.py"


@requires_git
def test_range_bad_ref_errors(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "x.py").write_text("a = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "first")

    res = _run_cli("--range", "nope..HEAD", cwd=tmp_path)
    assert res.returncode == 2
    assert "resolve" in res.stderr


@requires_git
def test_config_extra_surface_changes_score(tmp_path: Path) -> None:
    _repo(tmp_path)
    pay = tmp_path / "payments"
    pay.mkdir()
    (pay / "charge.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (pay / "charge.py").write_text("x = 2\n")
    _git(tmp_path, "add", "-A")

    # Without config: payments/ is not sensitive -> SAVR.
    plain = _run_cli("--staged", "--no-color", "--no-config", cwd=tmp_path)
    assert "SAVR" in plain.stdout

    # With config marking payments/ sensitive -> a signal fires.
    (tmp_path / ".sommelier.toml").write_text(
        '[[surface]]\npattern = "(^|/)payments/"\npoints = 30\n'
        'reason = "touches the payments module"\n'
    )
    tuned = _run_cli("--staged", "--no-color", cwd=tmp_path)
    assert "payments module" in tuned.stdout


def test_bad_config_path_errors() -> None:
    res = _run_cli("--config", "/does/not/exist.toml", "--no-color", stdin="")
    assert res.returncode == 2
    assert "config file not found" in res.stderr


def test_stdin_still_works_with_no_config() -> None:
    diff = "\n".join(
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
    res = _run_cli("--no-config", "--no-color", stdin=diff)
    assert res.returncode == 0
    assert "auth/login.py:1" in res.stdout
    assert "GULP" in res.stdout
