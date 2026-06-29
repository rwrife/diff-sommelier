"""diff-sommelier 🍷 — triage your code-review attention.

A reviewer-side tool that reads a unified diff and (eventually) ranks every
hunk by how risky and surprising it is, so you read the dangerous parts first.

v0.1 is being built milestone-by-milestone; see PLAN.md. This package currently
provides the CLI scaffold (M1), the typed unified-diff parser (M2,
:mod:`diff_sommelier.parser`), the heuristic scoring engine (M3,
:mod:`diff_sommelier.rules` + :mod:`diff_sommelier.scorer`), the human +
JSON "tasting menu" presenters (M4, :mod:`diff_sommelier.render`), and the
attention budget + CI gate (M5, :mod:`diff_sommelier.budget`).
"""

from __future__ import annotations

__version__ = "0.1.0"

from diff_sommelier.budget import (
    Budget,
    BudgetResult,
    TimeModel,
    apply_budget,
    fail_over,
    parse_budget,
)
from diff_sommelier.parser import ChangeType, Diff, File, Hunk, parse_diff
from diff_sommelier.render import Tier, render_human, render_json, tier_for
from diff_sommelier.rules import Signal
from diff_sommelier.scorer import ScoredHunk, score_diff, score_hunk

__all__ = [
    "__version__",
    "ChangeType",
    "Diff",
    "File",
    "Hunk",
    "parse_diff",
    "Signal",
    "ScoredHunk",
    "score_diff",
    "score_hunk",
    "Tier",
    "tier_for",
    "render_human",
    "render_json",
    "Budget",
    "BudgetResult",
    "TimeModel",
    "apply_budget",
    "parse_budget",
    "fail_over",
]
