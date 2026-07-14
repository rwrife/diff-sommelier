"""End-to-end tests for --no-tests (issue #32).

Runs the CLI with a piped diff and asserts the no-tests signal fires for a
risky source hunk lacking test changes, stays silent when a related test file is
touched, and no-ops without the flag.
"""

from __future__ import annotations

import json
import sys


def _run_cli(*args: str, stdin: str):
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "diff_sommelier", *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def _big(path: str, lines: int = 120) -> str:
    body = "".join(f"+line {i}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1,{lines} @@\n-old\n{body}"
    )


def _tiny(path: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


def _no_tests_reasons(stdout: str) -> list[str]:
    data = json.loads(stdout)
    hunks = data["hunks"] if isinstance(data, dict) and "hunks" in data else data
    out = []
    for h in hunks:
        for sig in h.get("signals", []):
            if sig.get("rule") == "no-tests":
                out.append(sig["reason"])
    return out


def test_flag_off_no_signal():
    res = _run_cli("--json", stdin=_big("src/app.py"))
    assert res.returncode == 0
    assert _no_tests_reasons(res.stdout) == []


def test_risky_source_no_tests_fires():
    res = _run_cli("--no-tests", "--json", stdin=_big("src/app.py"))
    assert res.returncode == 0
    reasons = _no_tests_reasons(res.stdout)
    assert len(reasons) == 1
    assert "no test changes" in reasons[0]


def test_related_test_touch_silences():
    diff = _big("src/app.py") + _tiny("tests/test_app.py")
    res = _run_cli("--no-tests", "--json", stdin=diff)
    assert res.returncode == 0
    assert _no_tests_reasons(res.stdout) == []
