"""Tests for the opt-in CODEOWNERS ownership rule (:mod:`diff_sommelier.owners`).

Exercises the pure pieces with an **injectable CODEOWNERS body** (via
``build_index(text=...)``, so no filesystem is needed for the parse/match logic),
plus a couple of on-disk checks that the three standard locations are discovered.
Mirrors the hotspots/blast-radius test strategy.
"""

from __future__ import annotations

from pathlib import Path

from diff_sommelier import owners
from diff_sommelier.config import Config, load_config
from diff_sommelier.owners import (
    OwnersIndex,
    append_rule,
    build_index,
    make_rule,
    make_rule_or_none,
)
from diff_sommelier.parser import parse_diff


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _hunk_for(path: str):
    """A minimal one-line-change hunk against ``path`` (content is irrelevant)."""
    text = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    diff = parse_diff(text)
    return diff.hunks[0], diff.files[0]


def _index(text: str, *, root="/repo") -> OwnersIndex:
    idx = build_index(root, text=text)
    assert idx is not None
    return idx


CODEOWNERS = """\
# comment line, ignored
*           @global-owner
*.py        @py-team
/src/api/   @team-payments
docs/       @docs-team
"""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_build_index_parses_entries_and_skips_comments():
    idx = _index(CODEOWNERS)
    assert len(idx.entries) == 4
    assert idx.entries[0].pattern == "*"
    assert idx.entries[1].owners == ("@py-team",)


def test_build_index_none_when_empty_or_missing():
    assert build_index("/repo", text="# only comments\n\n") is None
    assert build_index("/definitely/not/a/real/dir/xyz") is None


# --------------------------------------------------------------------------- #
# Matching + last-match-wins precedence
# --------------------------------------------------------------------------- #
def test_last_match_wins():
    idx = _index(CODEOWNERS)
    # src/api/pay.py matches "*", "*.py", and "/src/api/" — last wins.
    assert idx.owners_for("src/api/pay.py") == ("@team-payments",)
    # a plain .py elsewhere: last matching is "*.py".
    assert idx.owners_for("lib/util.py") == ("@py-team",)
    # a non-py file: only "*" matches.
    assert idx.owners_for("README") == ("@global-owner",)


def test_directory_pattern_matches_subtree():
    idx = _index(CODEOWNERS)
    assert idx.owners_for("docs/guide/intro.md") == ("@docs-team",)


def test_unowned_when_no_entry_matches():
    idx = _index("/src/api/  @team-payments\n")
    assert idx.owners_for("frontend/app.ts") is None


# --------------------------------------------------------------------------- #
# Rule behaviour
# --------------------------------------------------------------------------- #
def test_author_owned_file_is_skipped():
    idx = _index(CODEOWNERS)
    rule = make_rule(idx, "@py-team")
    hunk, file = _hunk_for("lib/util.py")
    assert list(rule(hunk, file)) == []


def test_author_owned_case_insensitive():
    idx = _index(CODEOWNERS)
    rule = make_rule(idx, "@PY-Team")
    hunk, file = _hunk_for("lib/util.py")
    assert list(rule(hunk, file)) == []


def test_other_owned_boosts_with_reason():
    idx = _index(CODEOWNERS)
    rule = make_rule(idx, "@py-team")
    hunk, file = _hunk_for("src/api/pay.py")
    signals = list(rule(hunk, file))
    assert len(signals) == 1
    assert signals[0].rule == "owners"
    assert signals[0].points == owners._POINTS_OTHER_OWNED
    assert "@team-payments" in signals[0].reason
    assert "not the author" in signals[0].reason


def test_unowned_boosts_higher_with_reason():
    idx = _index("/src/api/  @team-payments\n")
    rule = make_rule(idx, "@someone")
    hunk, file = _hunk_for("frontend/app.ts")
    signals = list(rule(hunk, file))
    assert len(signals) == 1
    assert signals[0].points == owners._POINTS_UNOWNED
    assert signals[0].points > owners._POINTS_OTHER_OWNED
    assert "unowned" in signals[0].reason.lower()


# --------------------------------------------------------------------------- #
# make_rule_or_none / append_rule wiring
# --------------------------------------------------------------------------- #
def test_make_rule_or_none_needs_both_index_and_author():
    idx = _index(CODEOWNERS)
    assert make_rule_or_none(None, "@a") is None
    assert make_rule_or_none(idx, None) is None
    assert make_rule_or_none(idx, "") is None
    assert make_rule_or_none(idx, "@a") is not None


def test_append_rule_noop_without_index():
    base = [lambda h, f: []]
    assert append_rule(base, None, "@a") == base


def test_append_rule_applies_weight():
    idx = _index("* @other\n")
    calls = {}

    def weight(name, rule):
        calls["name"] = name
        return rule

    out = append_rule([], idx, "@me", weight=weight)
    assert len(out) == 1
    assert calls["name"] == "owners"


# --------------------------------------------------------------------------- #
# Standard-location discovery on disk
# --------------------------------------------------------------------------- #
def test_discovers_github_codeowners(tmp_path: Path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @root-owner\n")
    idx = build_index(str(tmp_path))
    assert idx is not None
    assert idx.owners_for("anything.txt") == ("@root-owner",)


def test_discovers_docs_codeowners(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CODEOWNERS").write_text("* @docs-root\n")
    idx = build_index(str(tmp_path))
    assert idx is not None
    assert idx.owners_for("x") == ("@docs-root",)


def test_github_location_wins_over_others(tmp_path: Path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @github-loc\n")
    (tmp_path / "CODEOWNERS").write_text("* @root-loc\n")
    idx = build_index(str(tmp_path))
    assert idx is not None
    assert idx.owners_for("x") == ("@github-loc",)


# --------------------------------------------------------------------------- #
# Config weight integration
# --------------------------------------------------------------------------- #
def test_weight_tunes_owners_points():
    idx = _index("* @other\n")
    cfg = Config(weights={"owners": 2.0})
    hunk, file = _hunk_for("anything.py")
    [rule] = append_rule([], idx, "@me", weight=cfg.apply_weight)
    signals = list(rule(hunk, file))
    assert len(signals) == 1
    assert signals[0].points == owners._POINTS_OTHER_OWNED * 2


def test_owners_is_a_valid_weight_key(tmp_path: Path):
    (tmp_path / ".sommelier.toml").write_text("[weights]\nowners = 1.5\n")
    cfg = load_config(explicit=tmp_path / ".sommelier.toml")
    assert cfg.weights["owners"] == 1.5
