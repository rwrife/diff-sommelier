"""End-to-end tests for --explain-llm (issue #7).

Runs the CLI as a subprocess with ``SOMMELIER_LLM_BACKEND=echo`` — the offline,
deterministic demo backend — so nothing touches a network. Covers: the flag is
off by default (no model notes), the unconfigured error path exits non-zero, the
echo backend labels notes without changing the score, ``--explain-llm-top``
bounds how many hunks are sent, and the JSON contract carries the note as a
zero-point ``llm`` signal.
"""

from __future__ import annotations

import json
import os
import sys

_SAMPLE = (
    "diff --git a/auth/login.py b/auth/login.py\n"
    "--- a/auth/login.py\n"
    "+++ b/auth/login.py\n"
    "@@ -10,3 +10,4 @@ def login(u, p):\n"
    "-    if check(u, p):\n"
    "+    if check(u, p) or u == 'admin':\n"
    "+        eval(p)\n"
    "         return token(u)\n"
    "diff --git a/README.md b/README.md\n"
    "--- a/README.md\n"
    "+++ b/README.md\n"
    "@@ -1,2 +1,3 @@\n"
    " # Title\n"
    "+A new line.\n"
)


def _run_cli(*args: str, stdin: str, env: dict[str, str] | None = None):
    import subprocess

    full_env = dict(os.environ)
    # Start from a clean slate so a developer's real key can't affect the test.
    full_env.pop("SOMMELIER_LLM_BACKEND", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "diff_sommelier", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=full_env,
    )


# --------------------------------------------------------------------------- #
# Off by default
# --------------------------------------------------------------------------- #
def test_no_flag_means_no_model_notes():
    res = _run_cli("--no-color", stdin=_SAMPLE)
    assert res.returncode == 0
    assert "model:" not in res.stdout


def test_flag_without_backend_errors_nonzero():
    res = _run_cli("--explain-llm", "--no-color", stdin=_SAMPLE)
    assert res.returncode == 2
    assert "SOMMELIER_LLM_BACKEND" in res.stderr
    assert "model:" not in res.stdout


# --------------------------------------------------------------------------- #
# Echo backend: labelled notes, score preserved
# --------------------------------------------------------------------------- #
def test_echo_backend_labels_notes_in_human_menu():
    res = _run_cli(
        "--explain-llm", "--no-color", stdin=_SAMPLE, env={"SOMMELIER_LLM_BACKEND": "echo"}
    )
    assert res.returncode == 0
    assert "model:" in res.stdout
    assert "[echo]" in res.stdout
    # The top hunk keeps its heuristic score of 80 despite the added note.
    assert "80" in res.stdout


def test_explain_llm_top_bounds_hunks_sent():
    # top 1 → only the riskiest (auth) hunk gets a note; README stays note-free.
    res = _run_cli(
        "--json",
        "--explain-llm",
        "--explain-llm-top",
        "1",
        stdin=_SAMPLE,
        env={"SOMMELIER_LLM_BACKEND": "echo"},
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert len(data) == 2
    auth = next(h for h in data if h["file"] == "auth/login.py")
    readme = next(h for h in data if h["file"] == "README.md")
    assert any(s["rule"] == "llm" for s in auth["signals"])
    assert all(s["rule"] != "llm" for s in readme["signals"])


# --------------------------------------------------------------------------- #
# JSON contract: note is a zero-point llm signal; score/raw unchanged
# --------------------------------------------------------------------------- #
def test_json_note_is_zero_point_llm_signal():
    plain = _run_cli("--json", stdin=_SAMPLE)
    enriched = _run_cli(
        "--json", "--explain-llm", stdin=_SAMPLE, env={"SOMMELIER_LLM_BACKEND": "echo"}
    )
    assert plain.returncode == 0 and enriched.returncode == 0

    plain_top = json.loads(plain.stdout)[0]
    enriched_top = json.loads(enriched.stdout)[0]

    # Same hunk, same score and raw points — enrichment doesn't re-score.
    assert enriched_top["id"] == plain_top["id"]
    assert enriched_top["score"] == plain_top["score"]
    assert enriched_top["raw"] == plain_top["raw"]

    llm_signals = [s for s in enriched_top["signals"] if s["rule"] == "llm"]
    assert len(llm_signals) == 1
    assert llm_signals[0]["points"] == 0
    assert llm_signals[0]["reason"].startswith("model: ")


def test_unknown_backend_errors_nonzero():
    res = _run_cli(
        "--explain-llm", "--no-color", stdin=_SAMPLE, env={"SOMMELIER_LLM_BACKEND": "bogus"}
    )
    assert res.returncode == 2
    assert "unknown LLM backend" in res.stderr
