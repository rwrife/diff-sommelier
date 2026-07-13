"""Heuristic scoring rules (M3).

Every rule is a small, independent function that inspects a single
:class:`~diff_sommelier.parser.Hunk` (in the context of its
:class:`~diff_sommelier.parser.File`) and returns zero or more
:class:`Signal` objects. A signal is a *weighted, explained* observation:
"this hunk deletes a lot of lines" (+points, with a human-readable reason).

The design goal is **transparency**: there is no magic model. Each point on a
hunk's final score traces back to a named rule and a sentence you can read.
:mod:`diff_sommelier.scorer` sums the signals and normalizes them to 0-100,
but it never invents points the rules didn't emit.

Rules live in submodules (:mod:`~diff_sommelier.rules.size`,
:mod:`~diff_sommelier.rules.surface`, :mod:`~diff_sommelier.rules.danger`) and
are collected in :data:`ALL_RULES`. Adding a new rule is the extension surface
for the entire backlog: write a function with the :data:`Rule` shape, append it
here, and it automatically participates in scoring and explanations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from diff_sommelier.parser import File, Hunk

__all__ = [
    "Signal",
    "Rule",
    "ALL_RULES",
    "run_rules",
]


@dataclass(frozen=True)
class Signal:
    """A single weighted, explained observation about a hunk.

    Attributes:
        rule: The name of the rule that produced the signal (e.g. ``"size"``).
        points: How many raw risk points this observation contributes. Always
            non-negative; rules add risk, they never subtract it.
        reason: A short, human-readable explanation of *why* this fired, shown
            verbatim in the "tasting menu" and JSON output.
    """

    rule: str
    points: int
    reason: str


# A rule takes a hunk and its owning file and yields zero or more signals.
# Passing the file too lets path-aware rules (surface) and file-status-aware
# rules (danger's "deleted file") see context the hunk alone doesn't carry.
Rule = Callable[[Hunk, File], Iterable[Signal]]


def _load_rules() -> list[Rule]:
    """Import and collect the built-in rules.

    Imported lazily inside a function (rather than at module top level) to keep
    the import graph acyclic: the rule submodules import :class:`Signal` from
    here, so we must finish defining it before importing them.
    """
    from diff_sommelier.rules import control, danger, size, surface

    return [size.score, surface.score, danger.score, control.score]


ALL_RULES: list[Rule] = _load_rules()


def run_rules(hunk: Hunk, file: File, rules: Iterable[Rule] | None = None) -> list[Signal]:
    """Run ``rules`` (default :data:`ALL_RULES`) over a hunk and collect signals.

    Returns the signals in rule order, dropping any zero-point signals so the
    output only ever lists observations that actually moved the score.
    """
    selected = ALL_RULES if rules is None else list(rules)
    signals: list[Signal] = []
    for rule in selected:
        for signal in rule(hunk, file):
            if signal.points > 0:
                signals.append(signal)
    return signals
