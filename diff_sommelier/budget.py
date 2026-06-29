"""Attention budget + CI gate (M5).

Scoring (M3) tells you *how risky* each hunk is and the menu (M4) lists them
most-risky-first. This module answers the two remaining reviewer questions:

* **"I have N minutes — where do I stop?"** :func:`apply_budget` walks the
  ranked hunks, charging each one an estimated **reading time**, and draws a
  cut line: everything at or above the line is *"review this"*, everything
  below is *"skim this"*. A budget can be expressed as **time** (``5m``,
  ``90s``, ``1m30s``) or as a **count** (``10hunks``, ``8``).
* **"Did a scary hunk sneak in?"** :func:`fail_over` returns the worst score in
  the diff so the CLI can exit non-zero when any hunk meets or exceeds a
  ``--fail-over <score>`` threshold — a one-line CI gate against unreviewed
  danger.

The reading-time model is deliberately simple, transparent, and *configurable*
(:class:`TimeModel`): a fixed per-hunk overhead (the cost of context-switching
to a new location) plus a per-changed-line cost (you read added and removed
lines). Risk does not change the *time* a hunk takes to read — it changes its
*rank* — so the budget spends your minutes on the most dangerous hunks first.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from diff_sommelier.scorer import ScoredHunk

__all__ = [
    "TimeModel",
    "Budget",
    "BudgetResult",
    "parse_budget",
    "estimate_seconds",
    "apply_budget",
    "fail_over",
    "format_duration",
    "BudgetError",
]


class BudgetError(ValueError):
    """Raised when a ``--budget`` spec can't be parsed."""


@dataclass(frozen=True)
class TimeModel:
    """How long a reviewer is assumed to spend on a hunk.

    The estimate for one hunk is::

        per_hunk_overhead_s + changed_lines * seconds_per_changed_line

    where ``changed_lines`` is the hunk's added + removed lines. Defaults are
    rough but defensible: ~8s to land on a new spot in the diff and orient,
    then ~1.5s per changed line of real reading. Both are exposed so a team can
    tune them (later via ``.sommelier.toml`` in M6, today via the API/tests).
    """

    seconds_per_changed_line: float = 1.5
    per_hunk_overhead_s: float = 8.0

    def __post_init__(self) -> None:
        if self.seconds_per_changed_line < 0 or self.per_hunk_overhead_s < 0:
            raise ValueError("time-model constants must be non-negative")

    def hunk_seconds(self, scored: ScoredHunk) -> float:
        """Estimated seconds to read one scored hunk."""
        return self.per_hunk_overhead_s + scored.hunk.changed * self.seconds_per_changed_line


# A reasonable default reading-time model, shared by the CLI when the caller
# doesn't supply one.
DEFAULT_TIME_MODEL = TimeModel()


@dataclass(frozen=True)
class Budget:
    """A parsed attention budget.

    Exactly one dimension is set: a **time** budget (``seconds``) or a **count**
    budget (``hunks``). Build one with :func:`parse_budget`.
    """

    seconds: float | None = None
    hunks: int | None = None

    @property
    def is_time(self) -> bool:
        return self.seconds is not None

    @property
    def is_count(self) -> bool:
        return self.hunks is not None


@dataclass(frozen=True)
class BudgetResult:
    """The outcome of applying a budget to a ranked hunk list.

    ``cut`` is the number of hunks that fit *within* the budget — i.e. rows
    ``[0:cut]`` are "review this" and rows ``[cut:]`` are "skim this". The
    budget is always honest about over/undershoot via the time/count totals.
    """

    cut: int
    total: int
    budget: Budget
    # Cumulative estimated seconds for the first ``cut`` hunks (time budgets) or
    # for all hunks (informational). Always populated so renderers can show a
    # realistic "≈ 4m30s above the line".
    spent_seconds: float
    total_seconds: float

    @property
    def reviewed(self) -> int:
        return self.cut

    @property
    def skimmed(self) -> int:
        return max(0, self.total - self.cut)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_COUNT_RE = re.compile(r"^\s*(\d+)\s*(?:hunks?)\s*$", re.IGNORECASE)
_BARE_INT_RE = re.compile(r"^\s*(\d+)\s*$")
# One or more <number><unit> pairs, units h/m/s. Also accepts a bare number of
# minutes via the dedicated branch below, but here we require explicit units.
_DURATION_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([hms])", re.IGNORECASE)
_DURATION_FULLMATCH_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)?\s*[hms]\s*)+$", re.IGNORECASE)

_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0}


def parse_budget(spec: str) -> Budget:
    """Parse a ``--budget`` string into a :class:`Budget`.

    Accepted forms (case-insensitive)::

        "5m"        -> 300 seconds
        "90s"       -> 90 seconds
        "1m30s"     -> 90 seconds
        "1h"        -> 3600 seconds
        "10hunks"   -> 10 hunks
        "10hunk"    -> 10 hunks
        "12"        -> 12 hunks   (a bare integer means hunks)

    Raises :class:`BudgetError` on anything else.
    """
    if spec is None:
        raise BudgetError("empty budget")
    text = spec.strip()
    if not text:
        raise BudgetError("empty budget")

    m = _COUNT_RE.match(text)
    if m:
        return Budget(hunks=_positive_count(m.group(1), text))

    if _DURATION_FULLMATCH_RE.match(text):
        seconds = 0.0
        for value, unit in _DURATION_TOKEN_RE.findall(text):
            seconds += float(value) * _UNIT_SECONDS[unit.lower()]
        if seconds <= 0:
            raise BudgetError(f"budget must be positive: {spec!r}")
        return Budget(seconds=seconds)

    m = _BARE_INT_RE.match(text)
    if m:
        # A bare integer is the hunk count (the most common quick budget).
        return Budget(hunks=_positive_count(m.group(1), text))

    raise BudgetError(f"unrecognized budget {spec!r}; use e.g. '5m', '90s', '1m30s', or '10hunks'")


def _positive_count(digits: str, spec: str) -> int:
    n = int(digits)
    if n <= 0:
        raise BudgetError(f"budget must be positive: {spec!r}")
    return n


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def estimate_seconds(
    scored: Sequence[ScoredHunk],
    model: TimeModel | None = None,
) -> float:
    """Total estimated reading time, in seconds, for ``scored`` hunks."""
    m = model or DEFAULT_TIME_MODEL
    return sum(m.hunk_seconds(s) for s in scored)


def apply_budget(
    scored: Sequence[ScoredHunk],
    budget: Budget,
    model: TimeModel | None = None,
) -> BudgetResult:
    """Compute the cut line for ``scored`` (ranked, most-risky-first).

    For a **count** budget the cut is simply ``min(hunks, len(scored))``.

    For a **time** budget we charge each hunk in rank order and include a hunk
    as long as the *cumulative* estimate stays within the budget. The
    highest-risk hunk is always included even if it alone exceeds the budget —
    skipping the single scariest hunk because it's long would defeat the
    purpose — but the result still reports the true ``spent_seconds`` so the
    overshoot is visible.
    """
    rows = list(scored)
    total = len(rows)
    m = model or DEFAULT_TIME_MODEL
    total_seconds = estimate_seconds(rows, m)

    if budget.is_count:
        cut = min(budget.hunks or 0, total)
        spent = estimate_seconds(rows[:cut], m)
        return BudgetResult(
            cut=cut,
            total=total,
            budget=budget,
            spent_seconds=spent,
            total_seconds=total_seconds,
        )

    # Time budget.
    limit = budget.seconds or 0.0
    spent = 0.0
    cut = 0
    for i, s in enumerate(rows):
        cost = m.hunk_seconds(s)
        if i == 0:
            # Always include the top-ranked (most dangerous) hunk.
            spent += cost
            cut = 1
            continue
        if spent + cost <= limit:
            spent += cost
            cut = i + 1
        else:
            break

    return BudgetResult(
        cut=cut,
        total=total,
        budget=budget,
        spent_seconds=spent,
        total_seconds=total_seconds,
    )


def fail_over(scored: Sequence[ScoredHunk], threshold: int) -> int | None:
    """Return the worst score that meets/exceeds ``threshold``, else ``None``.

    The CLI maps a non-``None`` result to a non-zero exit so CI can fail a PR
    that contains a hunk at or above the danger threshold. Because scores are
    absolute (M3), a given threshold means the same thing on every run.
    """
    worst: int | None = None
    for s in scored:
        if s.score >= threshold and (worst is None or s.score > worst):
            worst = s.score
    return worst


def format_duration(seconds: float) -> str:
    """Render a duration as a compact ``2m30s`` / ``45s`` / ``1h05m`` string."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins:02d}m" if mins else f"{hours}h"
