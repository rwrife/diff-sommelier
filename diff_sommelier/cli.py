"""Command-line entry point for diff-sommelier.

Currently it can:

* print ``--version``
* read a unified diff from **stdin** and report a count of files and hunks,
  now backed by the real typed parser in :mod:`diff_sommelier.parser` (M2).

Scoring, the ranked "tasting menu", and budget/CI gates arrive in later
milestones (M3+).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from diff_sommelier import __version__
from diff_sommelier.parser import parse_diff

PROG = "diff-sommelier"


@dataclass(frozen=True)
class DiffCounts:
    """Summary of a unified diff: how many files and hunks."""

    files: int
    hunks: int


def count_diff(lines: Iterable[str]) -> DiffCounts:
    """Count files and hunks in a unified diff using the real M2 parser.

    Kept as a thin convenience wrapper (and for the CLI's stdin summary) so the
    counts stay consistent with the typed model in
    :mod:`diff_sommelier.parser`.
    """
    diff = parse_diff(lines)
    return DiffCounts(files=len(diff.files), hunks=len(diff.hunks))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Triage your code-review attention: rank diff hunks by risk + "
            "surprise and tell you what to read first. (Reads a unified diff "
            "from stdin and reports file/hunk counts; scoring lands in M3+.)"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)

    if sys.stdin.isatty():
        # No piped diff; nothing to do yet. Point the user at --help.
        parser.print_usage()
        print(
            f"{PROG}: no diff on stdin. Pipe a unified diff, e.g. `git diff | {PROG}`.",
            file=sys.stderr,
        )
        return 0

    counts = count_diff(sys.stdin)
    file_word = "file" if counts.files == 1 else "files"
    hunk_word = "hunk" if counts.hunks == 1 else "hunks"
    print(f"Parsed {counts.files} {file_word}, {counts.hunks} {hunk_word}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
