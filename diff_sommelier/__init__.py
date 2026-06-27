"""diff-sommelier 🍷 — triage your code-review attention.

A reviewer-side tool that reads a unified diff and (eventually) ranks every
hunk by how risky and surprising it is, so you read the dangerous parts first.

v0.1 is being built milestone-by-milestone; see PLAN.md. This package currently
provides the CLI scaffold (M1) and the typed unified-diff parser (M2,
:mod:`diff_sommelier.parser`).
"""

from __future__ import annotations

__version__ = "0.1.0"

from diff_sommelier.parser import ChangeType, Diff, File, Hunk, parse_diff

__all__ = [
    "__version__",
    "ChangeType",
    "Diff",
    "File",
    "Hunk",
    "parse_diff",
]
