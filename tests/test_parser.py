"""Fixture-based tests for the typed unified-diff parser (M2).

Each ``.patch`` under ``tests/fixtures/`` exercises a specific edge case. We
assert on the typed model (paths, change types, line ranges, +/- counts, binary
flags) and on the stability/uniqueness of content-hash hunk IDs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diff_sommelier.parser import ChangeType, Diff, File, Hunk, parse_diff

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse(name: str) -> Diff:
    return parse_diff(load(name))


# --------------------------------------------------------------------------- #
# Basic modified-file parsing                                                  #
# --------------------------------------------------------------------------- #


def test_modified_two_files_structure() -> None:
    diff = parse("modified_two_files.patch")
    assert len(diff.files) == 2
    paths = [f.path for f in diff.files]
    assert paths == ["src/app.py", "README.md"]
    assert all(f.change_type is ChangeType.MODIFIED for f in diff.files)
    assert not any(f.is_binary for f in diff.files)


def test_modified_first_file_hunk_ranges_and_counts() -> None:
    app = parse("modified_two_files.patch").files[0]
    assert len(app.hunks) == 2

    h1 = app.hunks[0]
    assert (h1.old_start, h1.old_lines) == (1, 5)
    assert (h1.new_start, h1.new_lines) == (1, 6)
    assert h1.added == 2  # +import sys, +return 0
    assert h1.removed == 1  # -return 1
    assert h1.file_path == "src/app.py"

    h2 = app.hunks[1]
    assert (h2.old_start, h2.old_lines) == (20, 3)
    assert (h2.new_start, h2.new_lines) == (21, 4)
    assert h2.added == 1
    assert h2.removed == 0
    assert h2.heading.strip() == "def helper():"


def test_modified_second_file_default_single_line_count() -> None:
    readme = parse("modified_two_files.patch").files[1]
    assert readme.path == "README.md"
    assert len(readme.hunks) == 1
    h = readme.hunks[0]
    # "@@ -1 +1,2 @@" -> missing old count means 1.
    assert (h.old_start, h.old_lines) == (1, 1)
    assert (h.new_start, h.new_lines) == (1, 2)
    assert h.added == 1
    assert h.removed == 0


def test_file_and_diff_level_added_removed_aggregate() -> None:
    diff = parse("modified_two_files.patch")
    app = diff.files[0]
    assert app.added == 3 and app.removed == 1
    assert diff.added == 4  # 3 in app.py + 1 in README.md
    assert diff.removed == 1


# --------------------------------------------------------------------------- #
# New / deleted files                                                          #
# --------------------------------------------------------------------------- #


def test_new_file() -> None:
    diff = parse("new_file.patch")
    assert len(diff.files) == 1
    f = diff.files[0]
    assert f.path == "newmod/thing.py"
    assert f.change_type is ChangeType.ADDED
    assert f.is_new
    assert f.old_path is None
    assert f.new_mode == "100644"
    assert len(f.hunks) == 1
    h = f.hunks[0]
    assert (h.old_start, h.old_lines) == (0, 0)
    assert (h.new_start, h.new_lines) == (1, 3)
    assert h.added == 3
    assert h.removed == 0


def test_deleted_file() -> None:
    diff = parse("deleted_file.patch")
    assert len(diff.files) == 1
    f = diff.files[0]
    assert f.path == "old/legacy.py"
    assert f.change_type is ChangeType.DELETED
    assert f.is_delete
    assert f.new_path is None
    assert f.old_mode == "100644"
    h = f.hunks[0]
    assert (h.new_start, h.new_lines) == (0, 0)
    assert h.removed == 3
    assert h.added == 0


# --------------------------------------------------------------------------- #
# Renames / copies                                                            #
# --------------------------------------------------------------------------- #


def test_pure_rename_no_content_change() -> None:
    files = parse("rename.patch").files
    assert len(files) == 2
    pure = files[0]
    assert pure.change_type is ChangeType.RENAMED
    assert pure.is_rename
    assert pure.old_path == "src/old_name.py"
    assert pure.new_path == "src/new_name.py"
    assert pure.path == "src/new_name.py"
    assert pure.hunks == []  # 100% similarity, no body


def test_rename_with_edit() -> None:
    edited = parse("rename.patch").files[1]
    assert edited.change_type is ChangeType.RENAMED
    assert edited.old_path == "lib/util.py"
    assert edited.new_path == "lib/helpers.py"
    assert len(edited.hunks) == 1
    h = edited.hunks[0]
    assert h.added == 1
    assert h.removed == 1
    assert h.file_path == "lib/helpers.py"


def test_copy() -> None:
    f = parse("copy.patch").files[0]
    assert f.change_type is ChangeType.COPIED
    assert f.old_path == "templates/base.html"
    assert f.new_path == "templates/copy.html"
    assert f.path == "templates/copy.html"
    assert len(f.hunks) == 1
    assert f.hunks[0].added == 1
    assert f.hunks[0].removed == 1


# --------------------------------------------------------------------------- #
# Mode change / binary                                                        #
# --------------------------------------------------------------------------- #


def test_mode_change_only() -> None:
    f = parse("mode_change.patch").files[0]
    assert f.path == "scripts/run.sh"
    assert f.old_mode == "100644"
    assert f.new_mode == "100755"
    assert f.hunks == []
    assert not f.is_binary
    # No add/delete/rename markers -> classified as modified.
    assert f.change_type is ChangeType.MODIFIED


def test_binary_file() -> None:
    f = parse("binary.patch").files[0]
    assert f.path == "assets/logo.png"
    assert f.is_binary is True
    assert f.hunks == []


# --------------------------------------------------------------------------- #
# Plain (non-git) unified diff                                                 #
# --------------------------------------------------------------------------- #


def test_plain_unified_no_git_header() -> None:
    diff = parse("plain_unified.patch")
    assert len(diff.files) == 1
    f = diff.files[0]
    # No `diff --git`; path derived from the +++ header (timestamp stripped).
    assert f.path == "new.txt"
    assert f.old_path == "old.txt"
    assert len(f.hunks) == 1
    h = f.hunks[0]
    assert (h.old_start, h.old_lines) == (1, 3)
    assert (h.new_start, h.new_lines) == (1, 3)
    assert h.added == 1
    assert h.removed == 1


# --------------------------------------------------------------------------- #
# "No newline at end of file" marker                                          #
# --------------------------------------------------------------------------- #


def test_no_newline_marker_is_not_counted_as_change() -> None:
    f = parse("no_newline_eof.patch").files[0]
    assert f.path == "eof.txt"
    assert len(f.hunks) == 1
    h = f.hunks[0]
    # One real -/+ pair; the two "\ No newline" markers must NOT count.
    assert h.added == 1
    assert h.removed == 1
    assert "\\ No newline at end of file" in h.body


# --------------------------------------------------------------------------- #
# Hunk IDs: stable, unique, content-addressed                                  #
# --------------------------------------------------------------------------- #


def test_hunk_ids_are_stable_across_parses() -> None:
    a = parse("modified_two_files.patch").hunks
    b = parse("modified_two_files.patch").hunks
    assert [h.id for h in a] == [h.id for h in b]
    assert all(len(h.id) == 12 for h in a)


def test_hunk_ids_unique_within_diff() -> None:
    ids = [h.id for h in parse("modified_two_files.patch").hunks]
    assert len(ids) == len(set(ids))


def test_hunk_id_changes_when_body_changes() -> None:
    original = parse("modified_two_files.patch").files[0].hunks[0]
    mutated = Hunk(
        file_path=original.file_path,
        old_start=original.old_start,
        old_lines=original.old_lines,
        new_start=original.new_start,
        new_lines=original.new_lines,
        heading=original.heading,
        body=original.body + "\n+extra",
        added=original.added + 1,
        removed=original.removed,
    )
    assert mutated.id != original.id


def test_hunk_id_independent_of_position() -> None:
    """The same change at the same location hashes the same regardless of what
    other (unrelated) files precede it in the diff."""
    single = parse("new_file.patch").files[0].hunks[0]

    combined = parse_diff(load("modified_two_files.patch") + load("new_file.patch"))
    new_file_hunk = combined.files[-1].hunks[0]

    assert new_file_hunk.file_path == "newmod/thing.py"
    assert new_file_hunk.id == single.id


# --------------------------------------------------------------------------- #
# Header reconstruction + misc model helpers                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "modified_two_files.patch",
        "new_file.patch",
        "deleted_file.patch",
        "plain_unified.patch",
    ],
)
def test_header_property_roundtrips_ranges(name: str) -> None:
    for h in parse(name).hunks:
        rebuilt = h.header
        assert rebuilt.startswith("@@ -")
        assert f"+{h.new_start}" in rebuilt


def test_changed_is_added_plus_removed() -> None:
    for h in parse("modified_two_files.patch").hunks:
        assert h.changed == h.added + h.removed


def test_empty_input_yields_empty_diff() -> None:
    diff = parse_diff("")
    assert isinstance(diff, Diff)
    assert len(diff) == 0
    assert diff.hunks == []
    assert diff.added == 0 and diff.removed == 0


def test_diff_is_iterable_over_files() -> None:
    diff = parse("modified_two_files.patch")
    assert [f.path for f in diff] == [f.path for f in diff.files]
    assert all(isinstance(f, File) for f in diff)
