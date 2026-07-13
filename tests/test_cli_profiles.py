"""CLI-level tests for the --as reviewer-profile flag (issue #30)."""

from __future__ import annotations

import json
from pathlib import Path

from diff_sommelier.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def _run(capsys, argv, patch="profile_mix.patch", monkeypatch=None, stdin=None):
    text = (FIXTURES / patch).read_text()
    import io
    import sys

    sys.stdin = io.StringIO(text)
    try:
        code = main(argv)
    finally:
        sys.stdin = sys.__stdin__
    out = capsys.readouterr()
    return code, out.out, out.err


def test_as_flag_flips_top_hunk(capsys):
    _, backend_out, _ = _run(capsys, ["--as", "backend", "--json"])
    _, frontend_out, _ = _run(capsys, ["--as", "frontend", "--json"])
    backend = json.loads(backend_out)
    frontend = json.loads(frontend_out)
    assert backend[0]["file"] != frontend[0]["file"]
    assert backend[0]["file"].endswith("poetry.lock")
    assert frontend[0]["file"].endswith("main.css")


def test_as_comma_list_composes(capsys):
    code, out, _ = _run(capsys, ["--as", "backend,security", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data  # ran fine and produced ranked hunks


def test_as_unknown_profile_exits_2(capsys):
    code, _, err = _run(capsys, ["--as", "bogus", "--json"])
    assert code == 2
    assert "unknown profile 'bogus'" in err


def test_as_reason_is_annotated(capsys):
    code, out, _ = _run(capsys, ["--as", "security", "--json"])
    assert code == 0
    data = json.loads(out)
    reasons = [s["reason"] for h in data for s in h["signals"]]
    assert any("profile: security" in r for r in reasons)
