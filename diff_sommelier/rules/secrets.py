"""Opt-in, added-line secret/PII radar with value-only regex allowlisting."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Rule, Signal

RULE = "secrets"

_INLINE_ALLOW = re.compile(r"#\s*sommelier:\s*allow-secret\b")
_ASSIGNMENT = (
    r"\b(?:[\w-]*[_-])?(?:api[_-]?key|secret|token|passwd|password|access[_-]?key)"
    r"""['"]?\s*[:=]\s*"""
)
_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"(?<![A-Za-z0-9])AKIA[A-Z0-9]{16}(?![A-Za-z0-9])")),
    ("GitHub token", re.compile(r"(?<![\w])ghp_[A-Za-z0-9]{36}(?![\w])")),
    (
        "Slack token",
        re.compile(r"(?<![\w])xox[baprs]-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![\w])"),
    ),
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----"),
    ),
    (
        "JWT",
        re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![\w-])"),
    ),
    (
        "email/PII",
        re.compile(r"(?<![\w.+-])[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    ),
    (
        "API key/secret assignment",
        re.compile(
            _ASSIGNMENT + r"""(?P<quote>['"])(?P<value>[^'"\r\n]{6,})(?P=quote)""",
            re.I,
        ),
    ),
    (
        "API key/secret assignment",
        re.compile(
            _ASSIGNMENT + r"(?P<value>[A-Za-z0-9_+/=-]{6,})(?=\s*(?:$|[#;,}\]]))",
            re.I,
        ),
    ),
)
_GENERIC = re.compile(r"""(?P<quote>['"])(?P<value>[A-Za-z0-9_+/=-]{20,})(?P=quote)""")


def entropy(value: str) -> float:
    """Return Shannon entropy in bits per character (zero for empty input)."""
    if not value:
        return 0.0
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in Counter(value).values())


def make_rule(allow: Iterable[re.Pattern[str]] = ()) -> Rule:
    """Build a rule whose allow regexes match individual detected values.

    An inline marker suppresses the entire added line; a regex match suppresses
    only that value, never other findings on the same line.
    """
    patterns = tuple(allow)

    def allowed(value: str) -> bool:
        return any(pattern.search(value) for pattern in patterns)

    def score(hunk: Hunk, file: File) -> Iterator[Signal]:
        found: set[str] = set()
        for raw in hunk.body.splitlines():
            if not raw.startswith("+"):
                continue
            line = raw[1:]
            if _INLINE_ALLOW.search(line):
                continue
            detected: list[tuple[int, int]] = []
            for name, pattern in _DETECTORS:
                for match in pattern.finditer(line):
                    group = "value" if "value" in match.groupdict() else 0
                    value = match.group(group)
                    detected.append(match.span(group))
                    if not allowed(value):
                        found.add(name)
            for match in _GENERIC.finditer(line):
                start, end = match.span("value")
                if any(start < stop and end > begin for begin, stop in detected):
                    continue
                value = match.group("value")
                if (
                    any(c.isalpha() for c in value)
                    and any(c.isdigit() for c in value)
                    and entropy(value) >= 4.0
                    and not allowed(value)
                ):
                    found.add("high-entropy literal")
        for name in sorted(found):
            yield Signal(rule=RULE, points=60, reason=f"secrets: adds {name}")

    return score


score = make_rule()
