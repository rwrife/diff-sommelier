"""End-to-end tests for --owners (issue #26).

Runs the CLI with cwd set to a throwaway directory containing a CODEOWNERS file,
and asserts that a change to a file owned by *someone else* (or unowned) gets an
owners signal, while a file the --author owns does not. Also covers the graceful
no-op contract (no CODEOWNERS / no --author) and that a [weights] entry tunes it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _run_cli(*args: str, cwd: Path | None = None, stdin: str | None = None):
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "diff_sommelier", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    )


def _codeowners(tmp_path: Path) -> None:
    (tmp_path / "CODEOWNERS").write_text(
        "*        @global\n"
        "*.py     @py-team\n"
        "/secret/ @vault-team\n"
    )


def _owner_reasons(stdout: str) -> list[str]:
    data = json.loads(stdout)
    hunks = data["hunks"] if isinstance(data, dict) and "hunks" in data else data
    reasons: list[str] = []
    for h in hunks:
        for sig in h.get("signals", []):
            if sig.get("rule") == "owners":
                reasons.append(sig["reason"])
    return reasons


def test_other_owned_file_flagged(tmp_path: Path):
    _codeowners(tmp_path)
    res = _run_cli(
        "--owners", "--author", "@py-team", "--json",
        cwd=tmp_path, stdin=_diff("secret/keys.txt"),
    )
    assert res.returncode == 0, res.stderr
    reasons = _owner_reasons(res.stdout)
    assert any("@vault-team" in r for r in reasons)


def test_author_owned_file_not_flagged(tmp_path: Path):
    _codeowners(tmp_path)
    res = _run_cli(
        "--owners", "--author", "@py-team", "--json",
        cwd=tmp_path, stdin=_diff("app/util.py"),
    )
    assert res.returncode == 0, res.stderr
    assert _owner_reasons(res.stdout) == []


def test_noop_without_author(tmp_path: Path):
    _codeowners(tmp_path)
    res = _run_cli("--owners", "--json", cwd=tmp_path, stdin=_diff("secret/keys.txt"))
    assert res.returncode == 0, res.stderr
    assert _owner_reasons(res.stdout) == []


def test_noop_without_codeowners(tmp_path: Path):
    res = _run_cli(
        "--owners", "--author", "@me", "--json",
        cwd=tmp_path, stdin=_diff("secret/keys.txt"),
    )
    assert res.returncode == 0, res.stderr
    assert _owner_reasons(res.stdout) == []


def test_weight_zero_silences_owners(tmp_path: Path):
    _codeowners(tmp_path)
    (tmp_path / ".sommelier.toml").write_text("[weights]\nowners = 0\n")
    res = _run_cli(
        "--owners", "--author", "@py-team", "--json",
        cwd=tmp_path, stdin=_diff("secret/keys.txt"),
    )
    assert res.returncode == 0, res.stderr
    assert _owner_reasons(res.stdout) == []
