"""Typed unified-diff parser (M2).

This is the one module that must be correct: every later milestone scores off
the model it produces. The parser turns raw unified-diff text into typed
:class:`File` and :class:`Hunk` objects with:

* file-level status (added / deleted / modified / renamed / copied) and an
  ``is_binary`` flag,
* old/new mode information when present,
* per-hunk header text, old/new line ranges, ``+``/``-`` line counts, and the
  raw hunk body,
* a **stable, content-hash hunk ID** that depends only on the hunk's location
  and content (not on its position in the diff), so the same change hashes the
  same way across runs.

It is intentionally dependency-free (stdlib only) and tolerant of the common
shapes of diff output: ``git diff``, ``git format-patch`` bodies, and plain
``diff -u`` (no ``diff --git`` header).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "ChangeType",
    "Hunk",
    "File",
    "Diff",
    "parse_diff",
]

# ``@@ -l,s +l,s @@`` optionally followed by a section heading. The counts are
# optional in unified diffs (a missing count means 1).
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<heading>.*)$"
)

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$")


class ChangeType(StrEnum):
    """High-level classification of what happened to a file."""

    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"
    COPIED = "copied"


@dataclass(frozen=True)
class Hunk:
    """A single ``@@`` hunk within a file.

    ``old_start``/``new_start`` are 1-based line numbers in the old/new file.
    ``old_lines``/``new_lines`` are the line spans declared in the header. The
    ``added``/``removed`` counts are computed from the actual body so they stay
    correct even if a header lies (they usually agree).
    """

    file_path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    heading: str
    body: str
    added: int
    removed: int

    @property
    def header(self) -> str:
        """Reconstruct the canonical ``@@ -a,b +c,d @@`` header line."""
        old = f"{self.old_start}" if self.old_lines == 1 else f"{self.old_start},{self.old_lines}"
        new = f"{self.new_start}" if self.new_lines == 1 else f"{self.new_start},{self.new_lines}"
        if not self.heading or self.heading.startswith(" "):
            heading = self.heading
        else:
            heading = f" {self.heading}"
        return f"@@ -{old} +{new} @@{heading}"

    @property
    def id(self) -> str:
        """Stable content-hash ID for this hunk.

        Derived from the file path, the new-file start line, and the hunk body.
        It deliberately ignores the hunk's ordinal position in the diff, so an
        identical change in an identical location hashes identically across
        runs and across reorderings of unrelated hunks.
        """
        h = hashlib.sha1(usedforsecurity=False)
        h.update(self.file_path.encode("utf-8"))
        h.update(b"\0")
        h.update(str(self.new_start).encode("ascii"))
        h.update(b"\0")
        h.update(self.body.encode("utf-8"))
        return h.hexdigest()[:12]

    @property
    def changed(self) -> int:
        """Total changed (added + removed) lines in this hunk."""
        return self.added + self.removed


@dataclass
class File:
    """A single file's worth of changes (zero or more hunks)."""

    path: str
    change_type: ChangeType = ChangeType.MODIFIED
    old_path: str | None = None
    new_path: str | None = None
    old_mode: str | None = None
    new_mode: str | None = None
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(h.added for h in self.hunks)

    @property
    def removed(self) -> int:
        return sum(h.removed for h in self.hunks)

    @property
    def is_rename(self) -> bool:
        return self.change_type is ChangeType.RENAMED

    @property
    def is_new(self) -> bool:
        return self.change_type is ChangeType.ADDED

    @property
    def is_delete(self) -> bool:
        return self.change_type is ChangeType.DELETED


@dataclass
class Diff:
    """A parsed diff: an ordered list of :class:`File` objects."""

    files: list[File] = field(default_factory=list)

    @property
    def hunks(self) -> list[Hunk]:
        """All hunks across all files, in diff order."""
        return [h for f in self.files for h in f.hunks]

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def removed(self) -> int:
        return sum(f.removed for f in self.files)

    def __len__(self) -> int:
        return len(self.files)

    def __iter__(self):
        return iter(self.files)


def _strip_prefix(path: str) -> str:
    """Drop a leading ``a/`` or ``b/`` (and unquote git's C-quoted paths)."""
    if len(path) >= 2 and path[1] == "/" and path[0] in "ab":
        path = path[2:]
    if path == "/dev/null":
        return path
    # git quotes paths with special chars in double quotes; unquote minimally.
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        inner = path[1:-1]
        try:
            path = inner.encode("latin-1", "backslashreplace").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):  # pragma: no cover - defensive
            path = inner
    return path


def _split_records(lines: list[str]) -> list[list[str]]:
    """Split a git diff into per-file records starting at ``diff --git``.

    For plain (non-git) unified diffs that have no ``diff --git`` lines, a new
    record starts at each ``--- `` header.
    """
    records: list[list[str]] = []
    current: list[str] | None = None
    has_git_headers = any(line.startswith("diff --git ") for line in lines)

    for line in lines:
        if line.startswith("diff --git "):
            current = [line]
            records.append(current)
        elif not has_git_headers and line.startswith("--- "):
            current = [line]
            records.append(current)
        elif current is not None:
            current.append(line)
        # Lines before the first record header (e.g. commit message in a
        # format-patch body) are ignored.
    return records


def _parse_hunks(file_path: str, lines: list[str], start_idx: int) -> list[Hunk]:
    """Parse all hunks in ``lines`` starting at index ``start_idx``."""
    hunks: list[Hunk] = []
    i = start_idx
    n = len(lines)
    while i < n:
        m = _HUNK_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        old_start = int(m.group("old_start"))
        old_lines = int(m.group("old_count")) if m.group("old_count") is not None else 1
        new_start = int(m.group("new_start"))
        new_lines = int(m.group("new_count")) if m.group("new_count") is not None else 1
        heading = m.group("heading")

        body_lines: list[str] = []
        added = 0
        removed = 0
        i += 1
        while i < n:
            ln = lines[i]
            if _HUNK_HEADER_RE.match(ln):
                break
            # A new file record ends the current hunk body.
            if ln.startswith("diff --git "):
                break
            if ln.startswith("+++ ") or ln.startswith("--- "):
                # stray file header (shouldn't appear mid-hunk) -> stop.
                break
            if ln.startswith("\\"):
                # "\ No newline at end of file" — part of the body, not a change.
                body_lines.append(ln)
                i += 1
                continue
            if ln.startswith("+"):
                added += 1
            elif ln.startswith("-"):
                removed += 1
            elif ln and not ln.startswith(" "):
                # Unknown prefix; treat as end of this hunk's body.
                break
            body_lines.append(ln)
            i += 1

        hunks.append(
            Hunk(
                file_path=file_path,
                old_start=old_start,
                old_lines=old_lines,
                new_start=new_start,
                new_lines=new_lines,
                heading=heading,
                body="\n".join(body_lines),
                added=added,
                removed=removed,
            )
        )
    return hunks


def _parse_record(record: list[str]) -> File | None:
    """Parse one per-file record into a :class:`File`."""
    a_path: str | None = None
    b_path: str | None = None
    old_mode: str | None = None
    new_mode: str | None = None
    change_type: ChangeType | None = None
    is_binary = False
    minus_header: str | None = None
    plus_header: str | None = None
    hunks_start: int | None = None

    git_match = _DIFF_GIT_RE.match(record[0]) if record else None
    if git_match:
        a_path = _strip_prefix("a/" + git_match.group("a"))
        b_path = _strip_prefix("b/" + git_match.group("b"))

    for idx, line in enumerate(record):
        if line.startswith("old mode "):
            old_mode = line[len("old mode ") :].strip()
        elif line.startswith("new mode "):
            new_mode = line[len("new mode ") :].strip()
        elif line.startswith("deleted file mode "):
            change_type = ChangeType.DELETED
            old_mode = line[len("deleted file mode ") :].strip()
        elif line.startswith("new file mode "):
            change_type = ChangeType.ADDED
            new_mode = line[len("new file mode ") :].strip()
        elif line.startswith("rename from "):
            change_type = ChangeType.RENAMED
            a_path = _strip_prefix(line[len("rename from ") :].strip())
        elif line.startswith("rename to "):
            change_type = ChangeType.RENAMED
            b_path = _strip_prefix(line[len("rename to ") :].strip())
        elif line.startswith("copy from "):
            change_type = ChangeType.COPIED
            a_path = _strip_prefix(line[len("copy from ") :].strip())
        elif line.startswith("copy to "):
            change_type = ChangeType.COPIED
            b_path = _strip_prefix(line[len("copy to ") :].strip())
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            is_binary = True
        elif line.startswith("--- ") and minus_header is None:
            minus_header = _strip_prefix(line[len("--- ") :].split("\t", 1)[0].strip())
        elif line.startswith("+++ ") and plus_header is None:
            plus_header = _strip_prefix(line[len("+++ ") :].split("\t", 1)[0].strip())
        elif _HUNK_HEADER_RE.match(line) and hunks_start is None:
            hunks_start = idx

    # Resolve old/new paths from the most authoritative source available.
    old_path = a_path
    new_path = b_path
    if minus_header and minus_header != "/dev/null" and old_path is None:
        old_path = minus_header
    if plus_header and plus_header != "/dev/null" and new_path is None:
        new_path = plus_header

    # /dev/null markers pin add/delete classification.
    if minus_header == "/dev/null":
        change_type = ChangeType.ADDED
        old_path = None
    if plus_header == "/dev/null":
        change_type = ChangeType.DELETED
        new_path = None

    if change_type is None:
        change_type = ChangeType.MODIFIED

    # The canonical display path: prefer the new path, fall back to old.
    path = new_path or old_path
    if path is None:
        return None

    hunks: list[Hunk] = []
    if hunks_start is not None:
        hunks = _parse_hunks(path, record, hunks_start)

    return File(
        path=path,
        change_type=change_type,
        old_path=old_path,
        new_path=new_path,
        old_mode=old_mode,
        new_mode=new_mode,
        is_binary=is_binary,
        hunks=hunks,
    )


def parse_diff(text: str | Iterable[str]) -> Diff:
    """Parse unified-diff ``text`` (a string or iterable of lines) into a :class:`Diff`.

    Accepts ``git diff``, ``git format-patch`` bodies, and plain ``diff -u``
    output. Lines may or may not carry trailing newlines.
    """
    if isinstance(text, str):
        lines = text.splitlines()
    else:
        lines = [line.rstrip("\n") for line in text]

    diff = Diff()
    for record in _split_records(lines):
        parsed = _parse_record(record)
        if parsed is not None:
            diff.files.append(parsed)
    return diff
