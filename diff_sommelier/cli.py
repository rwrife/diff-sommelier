"""Command-line entry point for diff-sommelier (M1 scaffold).

This is intentionally minimal. It can:

* print ``--version``
* read a unified diff from **stdin** and echo a count of files and hunks

The counting here is a deliberate *placeholder* (simple line-prefix matching).
The real, typed unified-diff parser arrives in M2 (``diff_sommelier.parser``)
and will replace :func:`count_diff`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from diff_sommelier import __version__

PROG = "diff-sommelier"


@dataclass(frozen=True)
class DiffCounts:
    """Placeholder summary of a unified diff: how many files and hunks."""

    files: int
    hunks: int


def count_diff(lines: Iterable[str]) -> DiffCounts:
    """Roughly count files and hunks in a unified diff.

    Placeholder heuristic (replaced by the real parser in M2):

    * A new **file** starts at a ``diff --git`` line, or at a ``+++ `` header
      when no ``diff --git`` line preceded it (e.g. plain ``diff -u`` output).
    * A new **hunk** starts at an ``@@`` hunk header.
    """
    files = 0
    hunks = 0
    saw_git_header_for_file = False

    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("diff --git "):
            files += 1
            saw_git_header_for_file = True
        elif line.startswith("+++ "):
            # Only count this as a new file if we didn't already count a
            # `diff --git` header for it (avoids double-counting git diffs).
            if not saw_git_header_for_file:
                files += 1
            saw_git_header_for_file = False
        elif line.startswith("@@"):
            hunks += 1

    return DiffCounts(files=files, hunks=hunks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Triage your code-review attention: rank diff hunks by risk + "
            "surprise and tell you what to read first. (M1 scaffold: reads a "
            "unified diff from stdin and reports file/hunk counts.)"
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
