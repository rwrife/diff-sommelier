"""Scoring engine (M3).

The scorer turns the raw, additive :class:`~diff_sommelier.rules.Signal` points
from the rule pack into a normalized **0-100 risk score per hunk**, while
preserving every signal so the score stays fully explainable.

Design choices:

* **Absolute, not relative.** A hunk's score depends only on *its own* signals,
  not on the other hunks in the diff. This keeps scores stable across runs and
  lets later milestones use a fixed ``--fail-over <score>`` CI threshold that
  means the same thing every time.
* **Saturating normalization.** Raw points are mapped through a soft, monotonic
  curve that approaches but never exceeds 100. A hunk with a couple of moderate
  signals lands mid-range; a hunk stacking several severe danger signals
  approaches 100 without a single rule needing to "know" about the 0-100 scale.
* **Transparent.** :attr:`ScoredHunk.signals` is the full list of contributing
  observations; :attr:`ScoredHunk.raw` is their summed points before
  normalization. Nothing is hidden.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from diff_sommelier.parser import Diff, Hunk
from diff_sommelier.rules import Rule, Signal, run_rules

__all__ = [
    "ScoredHunk",
    "normalize",
    "score_hunk",
    "score_diff",
    "REFERENCE_RAW",
]

# Raw points at which a hunk is considered "as risky as it practically gets":
# the normalization curve passes ~91 here and asymptotically approaches 100
# beyond it. Chosen so that one strong danger signal (~16-20) is clearly
# elevated, a couple of stacked severe signals are high, and the scale never
# saturates so early that everything pegs at 100.
REFERENCE_RAW = 45.0


def normalize(raw: int | float) -> int:
    """Map summed raw points to an integer 0-100 score via a saturating curve.

    Uses ``100 * (1 - exp(-raw / k))`` where ``k`` is chosen from
    :data:`REFERENCE_RAW`. The function is 0 at ``raw == 0``, strictly
    increasing, and asymptotically bounded by 100, so no finite pile of points
    ever exceeds the scale. The result is rounded to the nearest integer and
    clamped into ``[0, 100]`` defensively.
    """
    if raw <= 0:
        return 0
    # Solve k so that normalize(REFERENCE_RAW) ~= 91 (1 - 1/e^2.4).
    k = REFERENCE_RAW / 2.4
    value = 100.0 * (1.0 - math.exp(-raw / k))
    return max(0, min(100, round(value)))


@dataclass(frozen=True)
class ScoredHunk:
    """A hunk paired with its computed risk score and the reasons behind it."""

    hunk: Hunk
    score: int
    raw: int
    signals: list[Signal] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        """The human-readable reason strings, highest-impact first."""
        return [s.reason for s in self.signals]

    def to_dict(self) -> dict:
        """JSON-serializable view: hunk identity/location, score, and signals.

        This is the canonical machine contract consumed by ``--json`` (and, in
        later milestones, the budget cut and editor integrations).
        """
        return {
            "id": self.hunk.id,
            "file": self.hunk.file_path,
            "old_start": self.hunk.old_start,
            "new_start": self.hunk.new_start,
            "added": self.hunk.added,
            "removed": self.hunk.removed,
            "score": self.score,
            "raw": self.raw,
            "signals": [
                {"rule": s.rule, "points": s.points, "reason": s.reason} for s in self.signals
            ],
        }


def score_hunk(
    hunk: Hunk,
    file,
    rules: Iterable[Rule] | None = None,
) -> ScoredHunk:
    """Score a single hunk: run the rules, sum points, normalize, keep reasons.

    Signals are sorted by descending points so the most impactful reason is
    listed first in both the JSON and the eventual human view.
    """
    signals = run_rules(hunk, file, rules)
    signals.sort(key=lambda s: s.points, reverse=True)
    raw = sum(s.points for s in signals)
    return ScoredHunk(hunk=hunk, score=normalize(raw), raw=raw, signals=signals)


def score_diff(diff: Diff, rules: Iterable[Rule] | None = None) -> list[ScoredHunk]:
    """Score every hunk in ``diff``, returned most-risky-first.

    Sorting is by score descending, then by raw points (to break score ties
    that collapsed under rounding), then by hunk ID for a fully deterministic
    order regardless of input order.
    """
    selected = None if rules is None else list(rules)
    scored = [score_hunk(h, f, selected) for f in diff.files for h in f.hunks]
    scored.sort(key=lambda s: (-s.score, -s.raw, s.hunk.id))
    return scored
