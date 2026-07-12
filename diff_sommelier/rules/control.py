"""Control-flow & off-by-one rule (M3).

Where :mod:`~diff_sommelier.rules.danger` looks for *dangerous content* and
:mod:`~diff_sommelier.rules.size` for *bulk*, this rule looks for the small,
quiet edits that carry outsized bug risk: changes to **control flow**. The
scariest reviewer-side bugs rarely announce themselves with 200 changed lines —
they hide in a flipped comparison, a newly-swallowed exception, or a ``not``
that appeared or disappeared from a condition.

It scans **added** and **removed** body lines (not context) for edits touching:

* **Conditionals & boolean logic** — new/changed ``if``/``elif``/``else``,
  ``and``/``or``, ternaries.
* **Comparison-operator flips** — a ``<`` becoming ``<=`` (or ``>``/``>=``)
  across a paired add/remove is classic off-by-one bait.
* **Loop bounds & index arithmetic** — ``range(...)``, ``while``, and
  ``i+1`` / ``len(x)-1`` / slice-bound arithmetic.
* **Error handling** — added/removed ``try``/``except`` (bare ``except`` is
  worse), swallowed exceptions (``except ...: pass``), and changed ``raise``.
* **Early exits** — ``return``/``continue``/``break`` added or removed inside
  branches, which quietly reroute control.
* **Negation flips** — a ``not`` added to or removed from a condition:
  high-surprise, low-churn, easy to miss.

Every signal carries an explainable reason so it shows up verbatim in the
text/rich/json output. Each pattern fires at most once per hunk (with a count
suffix) so a single repeated token can't run up the score.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

RULE = "control"


def _split_lines(hunk: Hunk) -> tuple[list[str], list[str]]:
    """Return ``(added, removed)`` content lines (sans +/- marker) for a hunk.

    Context lines (leading space) are ignored: we only care about lines the
    change actually introduced or deleted.
    """
    added: list[str] = []
    removed: list[str] = []
    for line in hunk.body.split("\n"):
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _strip_comment(line: str) -> str:
    """Best-effort strip of a trailing line comment (``#`` / ``//``).

    Crude on purpose — it treats a ``#`` or ``//`` anywhere as the start of a
    comment. That can clip a hash inside a string literal, but for the purpose
    of "does this changed line touch control flow" a false-negative on a weird
    string is far better than scoring a comment-only edit as risky.
    """
    for marker in ("#", "//"):
        idx = line.find(marker)
        if idx != -1:
            line = line[:idx]
    return line


def _is_all_comments(lines: list[str]) -> bool:
    """True if every non-blank changed line is comment-only or blank."""
    for line in lines:
        if _strip_comment(line).strip():
            return False
    return True


# Patterns scanned against changed *code* (comment-stripped) lines. Each entry:
# (compiled pattern, points, reason). Ordered roughly by severity.
_CODE_PATTERNS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (
        re.compile(r"\bexcept\s*:|\bexcept\b[^:]*:\s*pass\b"),
        11,
        "adds/changes a bare or swallowed exception handler",
    ),
    (
        re.compile(r"\b(try|except|finally)\b"),
        6,
        "changes error-handling flow (try/except/finally)",
    ),
    (
        re.compile(r"\braise\b"),
        5,
        "changes a raise",
    ),
    (
        re.compile(r"\b(range\s*\(|while\b)"),
        6,
        "changes a loop bound (range/while) — off-by-one risk",
    ),
    (
        re.compile(r"(\blen\s*\([^)]*\)\s*[+\-]\s*\d+|\b[A-Za-z_]\w*\s*[+\-]\s*1\b)"),
        5,
        "changes index/bound arithmetic — off-by-one risk",
    ),
    (
        re.compile(r"\b(if|elif)\b|[^=!<>]=\s*.+\bif\b.+\belse\b"),
        4,
        "changes a conditional",
    ),
    (
        re.compile(r"\b(and|or)\b"),
        3,
        "changes boolean logic (and/or)",
    ),
    (
        re.compile(r"\b(return|continue|break)\b"),
        4,
        "adds/removes an early return/continue/break",
    ),
)

# Comparison operators, for detecting a *flip* across a paired add/remove.
_COMPARISONS = ("<=", ">=", "<", ">", "==", "!=")


def _fire_patterns(lines: list[str]) -> Iterator[Signal]:
    """Yield one signal per distinct code pattern that matches ``lines``."""
    code = "\n".join(_strip_comment(ln) for ln in lines)
    for pattern, points, reason in _CODE_PATTERNS:
        matches = pattern.findall(code)
        count = len(matches)
        if count:
            suffix = f" (x{count})" if count > 1 else ""
            yield Signal(rule=RULE, points=points, reason=f"{reason}{suffix}")


def _comparison_flip_signal(added: list[str], removed: list[str]) -> Signal | None:
    """Detect a comparison-operator flip between removed and added code.

    Fires when the *set* of comparison operators differs between the removed and
    added lines (e.g. a ``<`` disappears while a ``<=`` appears) — the textbook
    off-by-one edit. Comment text is stripped first so a comment mention of an
    operator can't trigger it.
    """

    def ops(lines: list[str]) -> set[str]:
        found: set[str] = set()
        for raw in lines:
            code = _strip_comment(raw)
            # Match longest operators first so "<=" isn't split into "<".
            for op in _COMPARISONS:
                if op in code:
                    found.add(op)
        return found

    added_ops = ops(added)
    removed_ops = ops(removed)
    if not added_ops or not removed_ops:
        return None
    if added_ops != removed_ops:
        gained = sorted(added_ops - removed_ops)
        lost = sorted(removed_ops - added_ops)
        if gained or lost:
            detail = f"{'/'.join(lost) or '·'} → {'/'.join(gained) or '·'}"
            return Signal(
                rule=RULE,
                points=9,
                reason=f"comparison operator changed ({detail}) — off-by-one risk",
            )
    return None


def _negation_flip_signal(added: list[str], removed: list[str]) -> Signal | None:
    """Detect a ``not`` appearing or disappearing across a paired edit."""

    def has_not(lines: list[str]) -> bool:
        return any(re.search(r"\bnot\b", _strip_comment(ln)) for ln in lines)

    added_not = has_not(added)
    removed_not = has_not(removed)
    if added_not != removed_not:
        direction = "added" if added_not else "removed"
        return Signal(
            rule=RULE,
            points=8,
            reason=f"negation {direction} on a condition — inverts control flow",
        )
    return None


def score(hunk: Hunk, file: File) -> Iterator[Signal]:
    """Yield control-flow signals for a hunk's changed lines."""
    added, removed = _split_lines(hunk)
    changed = added + removed
    if not changed or _is_all_comments(changed):
        return

    # Paired-edit heuristics first (they explain the highest-surprise flips).
    flip = _comparison_flip_signal(added, removed)
    if flip is not None:
        yield flip

    negation = _negation_flip_signal(added, removed)
    if negation is not None:
        yield negation

    # Then the per-line pattern signals over all changed (code) lines.
    yield from _fire_patterns(changed)
