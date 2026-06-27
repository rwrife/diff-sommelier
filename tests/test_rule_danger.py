"""Unit tests for the danger rule (M3)."""

from __future__ import annotations

from diff_sommelier.parser import ChangeType, File, Hunk
from diff_sommelier.rules import danger


def hunk_with(body: str, *, added: int = 1, removed: int = 0, path: str = "src/app.py") -> Hunk:
    """Build a hunk from a raw body string (lines should carry +/-/space)."""
    return Hunk(
        file_path=path,
        old_start=1,
        old_lines=max(1, removed),
        new_start=1,
        new_lines=max(1, added),
        heading="",
        body=body,
        added=added,
        removed=removed,
    )


MODIFIED = File(path="src/app.py", change_type=ChangeType.MODIFIED)
DELETED = File(path="src/app.py", change_type=ChangeType.DELETED)


def reasons(hunk: Hunk, file: File = MODIFIED) -> list[str]:
    return [s.reason for s in danger.score(hunk, file)]


def points_for(hunk: Hunk, file: File = MODIFIED) -> int:
    return sum(s.points for s in danger.score(hunk, file))


# --------------------------------------------------------------------------- #
# Deletions                                                                    #
# --------------------------------------------------------------------------- #


def test_clean_addition_is_not_dangerous() -> None:
    assert reasons(hunk_with("+x = 1\n+y = 2", added=2)) == []


def test_file_deletion_flagged() -> None:
    h = hunk_with("-old = 1", added=0, removed=1)
    assert any("deletes a file" in r for r in reasons(h, DELETED))


def test_large_deletion_flagged() -> None:
    body = "\n".join(f"-line{i}" for i in range(70))
    h = hunk_with(body, added=0, removed=70)
    assert any("large deletion" in r for r in reasons(h))


def test_net_deletion_flagged_mildly() -> None:
    h = hunk_with("+a\n-b\n-c", added=1, removed=2)
    rs = reasons(h)
    assert any("net deletion" in r for r in rs)


def test_balanced_edit_has_no_deletion_signal() -> None:
    h = hunk_with("+a\n-b", added=1, removed=1)
    assert not any("deletion" in r for r in reasons(h))


# --------------------------------------------------------------------------- #
# Content patterns (added lines only)                                          #
# --------------------------------------------------------------------------- #


def test_eval_flagged() -> None:
    assert any("eval/exec" in r for r in reasons(hunk_with("+result = eval(payload)")))


def test_removed_eval_is_not_flagged() -> None:
    # eval on a *removed* line should not be treated as newly added risk.
    h = hunk_with("-result = eval(payload)", added=0, removed=1)
    assert not any("eval/exec" in r for r in reasons(h))


def test_subprocess_and_shell_true_flagged() -> None:
    h = hunk_with("+subprocess.run(cmd, shell=True)")
    rs = reasons(h)
    assert any("shell/subprocess" in r for r in rs)
    assert any("shell=True" in r for r in rs)


def test_hardcoded_secret_flagged() -> None:
    h = hunk_with('+api_key = "abcdef123456"')
    assert any("secret-looking" in r for r in reasons(h))


def test_private_key_flagged_highest() -> None:
    h = hunk_with("+-----BEGIN RSA PRIVATE KEY-----")
    sigs = list(danger.score(h, MODIFIED))
    assert any("private key" in s.reason for s in sigs)
    assert max(s.points for s in sigs) >= 20


def test_tls_verification_disabled_flagged() -> None:
    assert any("TLS/cert" in r for r in reasons(hunk_with("+requests.get(u, verify=False)")))


def test_raw_sql_flagged() -> None:
    h = hunk_with('+cur.execute(f"SELECT * FROM users WHERE id={uid}")')
    assert any("raw SQL" in r for r in reasons(h))


def test_repeated_pattern_counts_once_with_multiplier() -> None:
    h = hunk_with("+eval(a)\n+eval(b)\n+eval(c)", added=3)
    sigs = [s for s in danger.score(h, MODIFIED) if "eval/exec" in s.reason]
    assert len(sigs) == 1
    assert "(x3)" in sigs[0].reason
    # Still scored once, not tripled.
    assert sigs[0].points == 16


def test_multiple_distinct_dangers_stack() -> None:
    h = hunk_with('+eval(x)\n+api_key = "abcdef123456"', added=2)
    rs = reasons(h)
    assert any("eval/exec" in r for r in rs)
    assert any("secret-looking" in r for r in rs)
    assert points_for(h) >= 16 + 18
