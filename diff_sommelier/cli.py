"""Command-line entry point for diff-sommelier.

It reads a unified diff from **stdin** and presents it as a ranked
**tasting menu** — hunks ordered most-risky-first, each with a risk tier
(savor / sip / gulp), a 0-100 score, a score bar, its ``file:line``, and the
one-line *why* (the rules that fired). Output modes:

* default — the human tasting menu (colour via :mod:`rich` when stdout is a
  terminal; deterministic plain text otherwise or with ``--no-color``);
* ``--json`` — the scored, explained hunks as a JSON array (the machine
  contract for agents, editors, and the budget/CI tooling).

``--budget 5m|90s|10hunks`` draws a cut line in the menu (review above, skim
below), and ``--fail-over <score>`` makes the process exit non-zero when any
hunk meets or exceeds the threshold, so CI can flag a scary unreviewed hunk.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from diff_sommelier import __version__
from diff_sommelier.budget import (
    BudgetError,
    BudgetResult,
    apply_budget,
    fail_over,
    parse_budget,
)
from diff_sommelier.parser import parse_diff
from diff_sommelier.render import render_human, render_json
from diff_sommelier.scorer import ScoredHunk, score_diff

PROG = "diff-sommelier"

# Exit code returned when --fail-over trips (a hunk met/exceeded the threshold).
# Distinct from 2, which argparse uses for usage errors.
EXIT_FAIL_OVER = 1


@dataclass(frozen=True)
class DiffCounts:
    """Summary of a unified diff: how many files and hunks."""

    files: int
    hunks: int


def count_diff(lines: Iterable[str]) -> DiffCounts:
    """Count files and hunks in a unified diff using the real M2 parser.

    Kept as a thin convenience wrapper so callers (and tests) can get the
    file/hunk tally consistent with the typed model in
    :mod:`diff_sommelier.parser`.
    """
    diff = parse_diff(lines)
    return DiffCounts(files=len(diff.files), hunks=len(diff.hunks))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Triage your code-review attention: rank diff hunks by risk + "
            "surprise and tell you what to read first. Reads a unified diff "
            "from stdin and prints a ranked 'tasting menu' (or scored hunks "
            "as JSON with --json)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "emit scored, explained hunks as a JSON array (most risky first) "
            "instead of the human tasting menu"
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="force plain-text output (no colour), even on a terminal",
    )
    parser.add_argument(
        "--budget",
        metavar="SPEC",
        default=None,
        help=(
            "draw a cut line in the menu: review the hunks above it, skim "
            "below. SPEC is a time ('5m', '90s', '1m30s') or a count "
            "('10hunks', or a bare integer). Ignored with --json."
        ),
    )
    parser.add_argument(
        "--fail-over",
        metavar="SCORE",
        type=int,
        default=None,
        help=(
            "exit non-zero if any hunk's risk score is >= SCORE (0-100), so CI "
            "can flag a scary hunk. Combine with --json in a pipeline."
        ),
    )
    return parser


def _resolve_budget(
    scored: Sequence[ScoredHunk],
    budget_spec: str | None,
) -> BudgetResult | None:
    """Parse the --budget spec and compute the cut over ``scored``.

    Returns ``None`` when no budget was requested. Raises
    :class:`~diff_sommelier.budget.BudgetError` for an invalid spec so the CLI
    can report it cleanly.
    """
    if budget_spec is None:
        return None
    return apply_budget(scored, parse_budget(budget_spec))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if sys.stdin.isatty():
        # No piped diff; nothing to do. Point the user at --help.
        parser.print_usage()
        print(
            f"{PROG}: no diff on stdin. Pipe a unified diff, e.g. `git diff | {PROG}`.",
            file=sys.stderr,
        )
        return 0

    # Read once so we can both render and run the --fail-over gate over the same
    # scored diff without re-parsing stdin.
    diff = parse_diff(sys.stdin)
    scored = score_diff(diff)

    # Colour only when asked for (default) AND stdout is a real terminal, so
    # piping the menu into a file or pager yields clean plain text.
    color = not args.no_color and sys.stdout.isatty()

    if args.json:
        print(render_json(scored))
    else:
        try:
            budget = _resolve_budget(scored, args.budget)
        except BudgetError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2
        print(render_human(scored, color=color, budget=budget))

    # CI gate: a non-None worst score means a hunk met/exceeded the threshold.
    if args.fail_over is not None:
        worst = fail_over(scored, args.fail_over)
        if worst is not None:
            print(
                f"{PROG}: fail-over tripped — a hunk scored {worst} (>= {args.fail_over}).",
                file=sys.stderr,
            )
            return EXIT_FAIL_OVER
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
