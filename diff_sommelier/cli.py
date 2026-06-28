"""Command-line entry point for diff-sommelier.

It reads a unified diff from **stdin** and presents it as a ranked
**tasting menu** — hunks ordered most-risky-first, each with a risk tier
(savor / sip / gulp), a 0-100 score, a score bar, its ``file:line``, and the
one-line *why* (the rules that fired). Output modes:

* default — the human tasting menu (colour via :mod:`rich` when stdout is a
  terminal; deterministic plain text otherwise or with ``--no-color``);
* ``--json`` — the scored, explained hunks as a JSON array (the machine
  contract for agents, editors, and the later budget/CI tooling).

The attention budget cut line and CI gate arrive in later milestones (M5+).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from diff_sommelier import __version__
from diff_sommelier.parser import parse_diff
from diff_sommelier.render import render_human, render_json
from diff_sommelier.scorer import score_diff

PROG = "diff-sommelier"


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
    return parser


def _render(lines: Iterable[str], *, as_json: bool, color: bool) -> str:
    """Parse + score stdin and render it as JSON or the human tasting menu."""
    diff = parse_diff(lines)
    scored = score_diff(diff)
    if as_json:
        return render_json(scored)
    return render_human(scored, color=color)


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

    # Colour only when asked for (default) AND stdout is a real terminal, so
    # piping the menu into a file or pager yields clean plain text.
    color = not args.no_color and sys.stdout.isatty()
    print(_render(sys.stdin, as_json=args.json, color=color))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
