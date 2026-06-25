"""diff-sommelier 🍷 — triage your code-review attention.

A reviewer-side tool that reads a unified diff and (eventually) ranks every
hunk by how risky and surprising it is, so you read the dangerous parts first.

v0.1 is being built milestone-by-milestone; see PLAN.md. This package currently
provides the CLI scaffold (M1).
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
