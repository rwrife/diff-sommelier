"""Diff acquisition (M6).

The earlier milestones only read a unified diff from **stdin**. This module adds
the other two everyday ways a reviewer holds a diff:

* ``--staged`` — what's in the index, i.e. ``git diff --cached``;
* ``--range A..B`` — the changes between two refs, i.e. ``git diff A..B``.

stdin still works exactly as before (and is how you pipe ``gh pr diff`` in). The
three sources are mutually exclusive; the CLI enforces that and this module just
acquires the raw text. Keeping acquisition here (rather than in ``cli.py``) keeps
the I/O boundary small and unit-testable: every function returns the raw diff as
a single string, and :class:`SourceError` carries a clean, user-facing message
when git isn't available, isn't a repo, or a ref is bad.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence

__all__ = [
    "SourceError",
    "read_stdin",
    "read_git",
    "git_diff_args",
]


class SourceError(RuntimeError):
    """A diff source could not be acquired (no git, not a repo, bad ref...).

    Carries a short message suitable for printing straight to ``stderr``.
    """


def read_stdin(stream) -> str:
    """Read the whole unified diff from ``stream`` (usually ``sys.stdin``)."""
    return stream.read()


def git_diff_args(*, staged: bool, range_spec: str | None) -> list[str]:
    """Build the ``git`` argv for the requested source.

    Exactly one of ``staged`` / ``range_spec`` is expected to be set by the
    caller; this function trusts that contract and simply maps it to arguments.
    ``--no-color`` is forced so the parser never sees ANSI escapes, and
    ``--no-ext-diff`` so a user's custom external differ can't reshape the
    output we rely on.
    """
    base = ["git", "--no-pager", "diff", "--no-color", "--no-ext-diff"]
    if staged:
        return [*base, "--cached"]
    if range_spec is not None:
        return [*base, range_spec]
    # No selector: the working-tree diff (parity with a bare `git diff`).
    return base


def read_git(
    *,
    staged: bool = False,
    range_spec: str | None = None,
    cwd: str | None = None,
    _runner=subprocess.run,
) -> str:
    """Acquire a diff by shelling out to ``git``.

    Parameters mirror the CLI flags: ``staged`` for the index, ``range_spec``
    for ``A..B``. ``cwd`` runs git in another directory (used by the end-to-end
    test). ``_runner`` is injectable for unit tests so we never need a real repo
    to exercise the error mapping.

    Raises :class:`SourceError` with a friendly message when git is missing, the
    directory isn't a repository, or the ref/range is invalid.
    """
    if shutil.which("git") is None and _runner is subprocess.run:
        raise SourceError("git not found on PATH; install git or pipe a diff on stdin instead.")

    argv = git_diff_args(staged=staged, range_spec=range_spec)
    try:
        proc = _runner(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise SourceError("git not found on PATH; install git or pipe a diff on stdin.") from exc

    if proc.returncode != 0:
        raise SourceError(_explain_git_failure(argv, proc.returncode, proc.stderr))
    return proc.stdout


def _explain_git_failure(argv: Sequence[str], code: int, stderr: str | None) -> str:
    """Turn a non-zero ``git`` exit into a short, actionable message."""
    detail = (stderr or "").strip().splitlines()
    first = detail[0] if detail else ""
    lowered = first.lower()
    if "not a git repository" in lowered:
        return "not a git repository (run inside a repo, or pipe a diff on stdin)."
    unresolved = ("unknown revision", "bad revision", "ambiguous argument")
    if any(token in lowered for token in unresolved):
        return f"git could not resolve that range/ref: {first}"
    pretty = " ".join(argv)
    if first:
        return f"`{pretty}` failed (exit {code}): {first}"
    return f"`{pretty}` failed (exit {code})."
