"""Synthetic-only detector, entropy, allowlist and ranking checks."""

from __future__ import annotations

import re

import pytest

from diff_sommelier.config import Config
from diff_sommelier.parser import parse_diff
from diff_sommelier.rules import secrets
from diff_sommelier.scorer import score_diff

AWS = "AKIA" + "EXAMPLE012345678"
GITHUB = "ghp_" + "0123456789AbCdEfGhIjKlMnOpQrStUvWxYz"
GENERIC = "aB3dE5gH7jK9mN2pQ4sT6vW8"


def diff_with(body: str):
    return parse_diff(
        (
            "diff --git a/src/widget.py b/src/widget.py\n"
            "--- a/src/widget.py\n+++ b/src/widget.py\n"
            f"@@ -1 +1 @@\n{body}\n"
        ).splitlines(keepends=True)
    )


def signals(body: str, *allow: str):
    diff = diff_with(body)
    rule = secrets.make_rule(re.compile(pattern) for pattern in allow)
    return list(rule(diff.hunks[0], diff.files[0]))


@pytest.mark.parametrize(
    ("value", "detector"),
    [
        (AWS, "AWS access key"),
        (GITHUB, "GitHub token"),
        ("xoxb-1234567890-0987654321-synthetic", "Slack token"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
        ("-----BEGIN ENCRYPTED PRIVATE KEY-----", "private key"),
        ("eyJhbGciOiJub25lIn0.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGlj", "JWT"),
        ("synthetic.person@example.invalid", "email/PII"),
        (GENERIC, "high-entropy literal"),
    ],
)
def test_detectors_name_only(value: str, detector: str) -> None:
    found = signals(f'+value = "{value}"')
    assert [s.reason for s in found] == [f"secrets: adds {detector}"]
    assert all(value not in s.reason for s in found)


@pytest.mark.parametrize(
    "line",
    [
        'api_key = "synthetic-only"',
        '"apiKey": "synthetic-only"',
        "SERVICE_API_KEY=synthetic-only",
        "client_secret: synthetic-only",
        'password = "abcdef123456"',
    ],
)
def test_obvious_assignments(line: str) -> None:
    assert any("assignment" in s.reason for s in signals("+" + line))


@pytest.mark.parametrize(
    "value",
    ["a" * 40, "ab12" * 10, "abcdefghijklmnopqrst", "01234567890123456789", "aB3dE5gH7jK9"],
)
def test_generic_noise_filtered(value: str) -> None:
    assert signals(f'+value = "{value}"') == []


def test_entropy_threshold() -> None:
    assert secrets.entropy("") == 0
    assert secrets.entropy("a" * 40) == 0
    assert secrets.entropy("ab12" * 10) == 2
    assert secrets.entropy("aB3dE5gH7j" * 2) < 4.0
    assert secrets.entropy("0123456789ABCDEF" * 2) == 4.0
    assert signals('+value = "0123456789ABCDEF0123456789ABCDEF"')
    assert secrets.entropy(GENERIC) >= 4.0
    assert signals(f'+value = "{GENERIC}"')


def test_only_added_lines_scanned() -> None:
    assert signals(f'-value = "{AWS}"\n value = "{GITHUB}"\n+value = 1') == []


def test_once_per_detector_per_hunk() -> None:
    found = signals(f'+a = "{AWS}"\n+b = "{AWS}"')
    assert len(found) == 1


@pytest.mark.parametrize("value", [AWS, GITHUB, GENERIC, "person@example.invalid"])
def test_regex_and_inline_suppression(value: str) -> None:
    assert signals(f'+value = "{value}"', "^" + re.escape(value) + "$") == []
    assert signals(f'+value = "{value}" # sommelier: allow-secret') == []


def test_allowlisted_assignment_not_rediscovered_as_generic() -> None:
    assert signals(f'+api_key = "{GENERIC}"', "^" + GENERIC + "$") == []


def test_allowlisted_passphrase_matches_whole_value() -> None:
    assert signals('+password = "synthetic pass phrase"', "^synthetic pass phrase$") == []


@pytest.mark.parametrize(
    "line",
    ["token = load_token()", "api_key = os.getenv('KEY')", 'password = ""'],
)
def test_dynamic_or_empty_assignment_is_not_a_literal(line: str) -> None:
    assert signals("+" + line) == []


def test_allowlist_does_not_hide_other_values_on_same_line() -> None:
    found = signals(f'+values = ["person@example.invalid", "{AWS}"]', r"@example\.invalid$")
    assert [s.reason for s in found] == ["secrets: adds AWS access key"]


def test_inline_marker_only_suppresses_its_line() -> None:
    found = signals(f'+a = "{GITHUB}" # sommelier: allow-secret\n+b = "{AWS}"')
    assert [s.reason for s in found] == ["secrets: adds AWS access key"]


def test_secret_ranks_above_ordinary_danger() -> None:
    diff = diff_with("+value = eval(payload)")
    diff.files.extend(diff_with(f'+value = "{AWS}"').files)
    scored = score_diff(diff, rules=Config().rules(secrets=True))
    assert scored[0].score >= 90
    assert scored[0].signals[0].rule == "secrets"
    assert scored[0].score > scored[1].score
    assert scored[1].signals[0].rule == "danger"
