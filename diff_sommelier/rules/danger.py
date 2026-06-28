"""Danger rule (M3).

Where :mod:`~diff_sommelier.rules.surface` judges a hunk by *where* it lives,
this rule judges it by *what it actually says*. It scans the hunk body for
content patterns that correlate with risk:

* **Deletions** — removing code (especially a whole file) is how behavior
  silently disappears; net-negative hunks get a small prior, big deletions more.
* **Dynamic execution** — newly added ``eval(`` / ``exec(`` / ``os.system`` /
  ``subprocess(... shell=True)`` / ``pickle.loads`` is classic foot-gun and
  injection surface.
* **Secret-ish literals** — added lines that look like a hardcoded API key,
  token, password, or private-key header.
* **Permission / privilege changes** — ``chmod 777``, ``sudo``, ``setuid``,
  loosened CORS/SSL verification.
* **Raw SQL** — added string-built SQL (``SELECT``/``INSERT``/... via
  concatenation or f-strings) hints at injection-prone queries.

Only **added** lines (``+``) are scanned for the content patterns — we care
about new risk being introduced, not risk that's being removed. Deletions are
handled separately as their own signal. Each distinct pattern fires at most once
per hunk (with a count in the reason) so a hunk can't run up an unbounded score
from one repeated token.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from diff_sommelier.parser import ChangeType, File, Hunk
from diff_sommelier.rules import Signal

RULE = "danger"

# Content patterns scanned against *added* body lines. Each: (pattern, points,
# reason-singular). The reason gets a count suffix when it fires more than once.
_CONTENT_PATTERNS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (
        re.compile(r"\b(eval|exec)\s*\(", re.I),
        16,
        "adds dynamic eval/exec",
    ),
    (
        re.compile(r"\b(os\.system|subprocess\.\w+|popen)\b", re.I),
        10,
        "adds a shell/subprocess call",
    ),
    (
        re.compile(r"\bshell\s*=\s*True\b", re.I),
        8,
        "uses shell=True",
    ),
    (
        re.compile(r"\bpickle\.(loads?|Unpickler)\b", re.I),
        10,
        "adds pickle deserialization",
    ),
    (
        re.compile(
            r"(api[_-]?key|secret|token|passwd|password|access[_-]?key)"
            r"\s*[:=]\s*['\"][^'\"]{6,}['\"]",
            re.I,
        ),
        18,
        "adds a hardcoded secret-looking literal",
    ),
    (
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
        20,
        "adds an embedded private key",
    ),
    (
        re.compile(r"\bchmod\s+(0?7[0-7][0-7]|[+-]?[ugoa]*\+?x)\b|\bchmod\s+777\b"),
        9,
        "changes file permissions",
    ),
    (
        re.compile(r"\b(sudo|setuid|seteuid|setgid)\b"),
        8,
        "invokes elevated privileges",
    ),
    (
        re.compile(
            r"verify\s*=\s*False|CERT_NONE|rejectUnauthorized\s*:\s*false|"
            r"InsecureSkipVerify|NODE_TLS_REJECT_UNAUTHORIZED",
            re.I,
        ),
        14,
        "disables TLS/cert verification",
    ),
    (
        re.compile(r"Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]?\*", re.I),
        8,
        "opens CORS to all origins",
    ),
    (
        re.compile(
            r"\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b.*\b(FROM|INTO|SET|TABLE)\b"
            r"|(execute|query)\s*\(\s*f?['\"].*\b(SELECT|INSERT|UPDATE|DELETE)\b",
            re.I,
        ),
        9,
        "adds raw SQL",
    ),
)


def _added_lines(hunk: Hunk) -> list[str]:
    """Return the added (``+``) content lines of a hunk, sans the ``+`` marker.

    The ``+++`` file header never appears in :attr:`Hunk.body` (the parser keeps
    it out), so a simple ``startswith("+")`` is safe here.
    """
    out: list[str] = []
    for line in hunk.body.split("\n"):
        if line.startswith("+"):
            out.append(line[1:])
    return out


def _deletion_signal(hunk: Hunk, file: File) -> Signal | None:
    """Risk from removing code: whole-file deletes and net-negative hunks."""
    if file.change_type is ChangeType.DELETED:
        return Signal(
            rule=RULE,
            points=12,
            reason="deletes a file",
        )
    if hunk.removed == 0:
        return None
    if hunk.removed >= 60:
        return Signal(rule=RULE, points=10, reason=f"large deletion: -{hunk.removed} lines")
    if hunk.removed > hunk.added:
        return Signal(
            rule=RULE,
            points=4,
            reason=f"net deletion (-{hunk.removed}/+{hunk.added})",
        )
    return None


def score(hunk: Hunk, file: File) -> Iterator[Signal]:
    """Yield danger signals from the hunk's removals and added content."""
    deletion = _deletion_signal(hunk, file)
    if deletion is not None:
        yield deletion

    added = _added_lines(hunk)
    if not added:
        return
    blob = "\n".join(added)
    for pattern, points, reason in _CONTENT_PATTERNS:
        matches = pattern.findall(blob)
        count = len(matches)
        if count:
            suffix = f" (x{count})" if count > 1 else ""
            yield Signal(rule=RULE, points=points, reason=f"{reason}{suffix}")
