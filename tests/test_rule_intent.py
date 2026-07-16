"""Unit tests for the intent-mismatch rule (--intent)."""

from __future__ import annotations

from diff_sommelier.intent import Intent, append_rule, make_rule
from diff_sommelier.parser import ChangeType, File, Hunk

FILE = File(path="x", change_type=ChangeType.MODIFIED)


def hunk_for(path: str, body: str = "+x = 1") -> Hunk:
    lines = [ln for ln in body.split("\n") if ln.startswith("+")]
    return Hunk(
        file_path=path,
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=len(lines) or 1,
        heading="",
        body=body,
        added=len(lines),
        removed=0,
    )


def reasons(intent_text, path, body="+x = 1"):
    intent = Intent.parse(intent_text)
    rule = make_rule(intent)
    return [s.reason for s in rule(hunk_for(path, body), FILE)]


def points(intent_text, path, body="+x = 1"):
    intent = Intent.parse(intent_text)
    rule = make_rule(intent)
    return [s.points for s in rule(hunk_for(path, body), FILE)]


def test_none_intent_is_a_noop() -> None:
    rule = make_rule(None)
    assert list(rule(hunk_for("auth/session.py"), FILE)) == []


def test_blank_intent_parses_to_none() -> None:
    assert Intent.parse("") is None
    assert Intent.parse("   ") is None
    # Only stopwords -> no usable keywords.
    assert Intent.parse("fix the and or to") is None


def test_matching_hunk_stays_quiet() -> None:
    # PR is about auth; a hunk in auth/ should NOT be flagged.
    assert reasons("fix login session expiry bug", "src/auth/session.py") == []


def test_mismatched_hunk_is_flagged_as_surprise() -> None:
    # PR claims a README typo fix; a migration hunk is a full mismatch.
    out = reasons(
        "fix typo in README",
        "db/migrations/0007_add_users.py",
        body="+def upgrade():\n+    table.create()",
    )
    assert len(out) == 1
    assert "README" in out[0] or "readme" in out[0].lower()
    assert "migrations" in out[0]


def test_full_mismatch_scores_higher_than_weak() -> None:
    full = points(
        "fix typo in README",
        "db/migrations/0007_add_users.py",
        body="+def upgrade(): pass",
    )
    assert full == [12]


def test_readme_hunk_matches_readme_intent() -> None:
    assert reasons("fix typo in README", "docs/README.md", body="+welcome text") == []


def test_intent_is_case_insensitive() -> None:
    assert reasons("Fix Login Session", "src/AUTH/Session.py") == []


def test_reason_echoes_intent_truncated() -> None:
    long_intent = "refactor the authentication layer " + "blah " * 40
    out = reasons(long_intent, "frontend/widgets/button.tsx", body="+render()")
    assert out
    assert "…" in out[0]


def test_append_rule_noop_when_intent_none() -> None:
    base = [lambda h, f: []]
    assert append_rule(base, None) == base


def test_append_rule_adds_when_intent_present() -> None:
    base: list = []
    out = append_rule(base, Intent.parse("fix typo"))
    assert len(out) == 1


def test_empty_hunk_tokens_no_signal() -> None:
    # A hunk with no meaningful tokens shouldn't be flagged (avoid noise).
    assert reasons("fix typo in README", "x", body="+   ") == []
