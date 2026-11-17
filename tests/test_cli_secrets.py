"""End-to-end opt-in, config and non-disclosing --secrets output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_rule_secrets import AWS, GENERIC


def run_cli(*args: str, body: str):
    text = (
        "diff --git a/src/widget.py b/src/widget.py\n"
        "--- a/src/widget.py\n+++ b/src/widget.py\n"
        f"@@ -1 +1 @@\n{body}\n"
    )
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "diff_sommelier", *args],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_opt_in_and_fail_over() -> None:
    body = f'+value = "{AWS}"'
    off = run_cli("--json", "--no-config", body=body)
    assert off.returncode == 0
    assert json.loads(off.stdout)[0]["signals"] == []
    on = run_cli("--secrets", "--json", "--no-config", "--fail-over", "90", body=body)
    assert on.returncode == 1
    assert json.loads(on.stdout)[0]["score"] >= 90
    assert AWS not in on.stdout + on.stderr


def test_allowlist_replaces_coarse_danger_and_keeps_other_dangers(tmp_path: Path) -> None:
    config = tmp_path / ".sommelier.toml"
    config.write_text(f'[secrets]\nallow = ["^{GENERIC}$"]\n', encoding="utf-8")
    result = run_cli(
        "--secrets",
        "--json",
        "--config",
        str(config),
        body=f'+api_key = "{GENERIC}"\n+eval(payload)',
    )
    assert result.returncode == 0
    found = json.loads(result.stdout)[0]["signals"]
    assert [s["reason"] for s in found] == ["adds dynamic eval/exec"]


def test_inline_marker_suppresses_legacy_secret_check() -> None:
    result = run_cli(
        "--secrets",
        "--json",
        "--no-config",
        body=f'+api_key = "{GENERIC}" # sommelier: allow-secret',
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)[0]["signals"] == []


def test_secrets_weight_can_mute(tmp_path: Path) -> None:
    config = tmp_path / ".sommelier.toml"
    config.write_text("[weights]\nsecrets = 0\n", encoding="utf-8")
    result = run_cli("--secrets", "--json", "--config", str(config), body=f'+v = "{AWS}"')
    assert result.returncode == 0
    assert json.loads(result.stdout)[0]["signals"] == []


@pytest.mark.parametrize("mode", ["--json", "--sarif", "--markdown", "--no-color"])
def test_renderers_do_not_echo_detected_values(mode: str) -> None:
    result = run_cli("--secrets", "--no-config", mode, body=f'+value = "{AWS}"')
    assert result.returncode == 0
    assert "AWS access key" in result.stdout
    assert AWS not in result.stdout + result.stderr
