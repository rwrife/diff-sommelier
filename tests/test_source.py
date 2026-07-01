"""Tests for diff acquisition (M6): the git argv builder, the error mapping
(without needing a real repo, via an injected runner), and an honest
end-to-end run over a throwaway git repository."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from diff_sommelier.source import (
    SourceError,
    git_diff_args,
    read_git,
    read_stdin,
)


def test_read_stdin_reads_whole_stream() -> None:
    import io

    assert read_stdin(io.StringIO("a\nb\n")) == "a\nb\n"


def test_git_diff_args_default_is_working_tree() -> None:
    args = git_diff_args(staged=False, range_spec=None)
    assert args[:3] == ["git", "--no-pager", "diff"]
    assert "--no-color" in args
    assert "--no-ext-diff" in args
    assert "--cached" not in args


def test_git_diff_args_staged_adds_cached() -> None:
    args = git_diff_args(staged=True, range_spec=None)
    assert "--cached" in args


def test_git_diff_args_range_appends_spec() -> None:
    args = git_diff_args(staged=False, range_spec="main..HEAD")
    assert args[-1] == "main..HEAD"


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_read_git_returns_stdout_on_success() -> None:
    def runner(argv, **kwargs):
        return _FakeProc(0, stdout="DIFF-OUTPUT")

    assert read_git(range_spec="a..b", _runner=runner) == "DIFF-OUTPUT"


def test_read_git_maps_not_a_repo() -> None:
    def runner(argv, **kwargs):
        return _FakeProc(128, stderr="fatal: not a git repository (or any of the parent dirs)")

    with pytest.raises(SourceError) as exc:
        read_git(_runner=runner)
    assert "not a git repository" in str(exc.value)


def test_read_git_maps_bad_revision() -> None:
    def runner(argv, **kwargs):
        return _FakeProc(128, stderr="fatal: ambiguous argument 'nope..HEAD': unknown revision")

    with pytest.raises(SourceError) as exc:
        read_git(range_spec="nope..HEAD", _runner=runner)
    msg = str(exc.value)
    assert "could not resolve" in msg


def test_read_git_generic_failure_includes_exit_code() -> None:
    def runner(argv, **kwargs):
        return _FakeProc(3, stderr="something else went wrong")

    with pytest.raises(SourceError) as exc:
        read_git(_runner=runner)
    assert "exit 3" in str(exc.value)


# --- End-to-end over a real temp repo (skipped if git is unavailable) --------

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")


@requires_git
def test_read_git_range_end_to_end(tmp_path: Path) -> None:
    """`--range` style acquisition returns a real diff between two commits."""
    _init_repo(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("a = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "first")
    f.write_text("a = 2\nb = 3\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "second")

    out = read_git(range_spec="HEAD~1..HEAD", cwd=str(tmp_path))
    assert "diff --git a/mod.py b/mod.py" in out
    assert "+b = 3" in out


@requires_git
def test_read_git_staged_end_to_end(tmp_path: Path) -> None:
    """`--staged` style acquisition returns the index diff only."""
    _init_repo(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("a = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "first")
    f.write_text("a = 1\nstaged = True\n")
    _git(tmp_path, "add", "-A")

    out = read_git(staged=True, cwd=str(tmp_path))
    assert "+staged = True" in out


@requires_git
def test_read_git_bad_repo_raises(tmp_path: Path) -> None:
    """Running outside a repo yields a friendly SourceError."""
    with pytest.raises(SourceError) as exc:
        read_git(cwd=str(tmp_path))
    assert "not a git repository" in str(exc.value)
