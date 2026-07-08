"""Tests for the SARIF 2.1.0 renderer (issue #24 — code-scanning annotations).

The SARIF renderer emits the ranked hunks as a code-scanning log that
``upload-sarif`` can post as inline PR annotations. Like the Markdown renderer
it is deterministic (no colour, no terminal probing, no timestamps), so we pin
its *shape* — the schema/version envelope, the single run, one result per hunk,
the tier→level mapping, the physical locations, and the driver rule catalog —
while staying robust to the exact scores the M3 rules assign (we assert
structure and tiers, not magic numbers).
"""

from __future__ import annotations

import json

from diff_sommelier.parser import parse_diff
from diff_sommelier.render import render_sarif as render_sarif_facade
from diff_sommelier.render.sarif import (
    SARIF_SCHEMA,
    SARIF_VERSION,
    SKIM_RULE_ID,
    TIER_LEVEL,
    render_sarif,
)
from diff_sommelier.render.tiers import Tier, tier_for
from diff_sommelier.scorer import score_diff

# Same engineered diff the Markdown/text renderer tests use: one hunk per tier.
#   - auth/login.py: hardcoded secret + eval in auth code  -> GULP (error)
#   - .github/workflows/ci.yml: CI surface touch           -> SIP  (warning)
#   - README.md: a one-line docs change                    -> SAVR (note)
MENU_DIFF = "\n".join(
    [
        "diff --git a/auth/login.py b/auth/login.py",
        "--- a/auth/login.py",
        "+++ b/auth/login.py",
        "@@ -1,2 +1,4 @@",
        " def login(u, p):",
        "-    return ok(u, p)",
        '+    API_KEY = "sk-live-abcd1234abcd1234abcd"',
        "+    if eval(u):",
        "+        return True",
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml",
        "--- a/.github/workflows/ci.yml",
        "+++ b/.github/workflows/ci.yml",
        "@@ -1,1 +1,2 @@",
        " name: CI",
        "+  run: deploy.sh",
        "diff --git a/README.md b/README.md",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1,1 +1,2 @@",
        " # Title",
        "+a docs line",
        "",
    ]
)


def _scored(diff_text: str = MENU_DIFF):
    return score_diff(parse_diff(diff_text))


def _sarif(diff_text: str = MENU_DIFF, **kwargs) -> dict:
    """Render and parse the SARIF log for a diff (kwargs pass through)."""
    return json.loads(render_sarif(_scored(diff_text), **kwargs))


# --- envelope / schema ----------------------------------------------------


def test_top_level_is_valid_sarif_210_envelope() -> None:
    log = _sarif()
    assert log["$schema"] == SARIF_SCHEMA
    assert log["version"] == SARIF_VERSION == "2.1.0"
    assert isinstance(log["runs"], list) and len(log["runs"]) == 1


def test_output_is_parseable_json() -> None:
    # Must be a JSON *object* (a SARIF log), not the JSON renderer's array.
    parsed = json.loads(render_sarif(_scored()))
    assert isinstance(parsed, dict)


def test_driver_identifies_the_tool_with_version_and_uri() -> None:
    driver = _sarif()["runs"][0]["tool"]["driver"]
    assert driver["name"] == "diff-sommelier"
    assert "github.com/rwrife/diff-sommelier" in driver["informationUri"]
    # The tool version is stamped from the package (present in-tree).
    assert driver["version"]


def test_empty_diff_is_a_valid_empty_run() -> None:
    log = json.loads(render_sarif([]))
    run = log["runs"][0]
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []
    assert log["version"] == "2.1.0"


# --- results: one per hunk, tier -> level ---------------------------------


def test_one_result_per_scored_hunk_in_ranked_order() -> None:
    scored = _scored()
    results = _sarif()["runs"][0]["results"]
    assert len(results) == len(scored) == 3
    # Results preserve the scorer's most-risky-first order (by location).
    uris = [r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results]
    assert uris == [s.hunk.file_path for s in scored]
    # Concretely: the gulp (auth) result comes before the sip (ci) result.
    assert uris.index("auth/login.py") < uris.index(".github/workflows/ci.yml")


def test_tier_maps_to_sarif_level_for_every_result() -> None:
    scored = _scored()
    results = _sarif()["runs"][0]["results"]
    for s, r in zip(scored, results, strict=True):
        assert r["level"] == TIER_LEVEL[tier_for(s.score)]
    # And the mapping itself is the documented one.
    assert TIER_LEVEL == {Tier.GULP: "error", Tier.SIP: "warning", Tier.SAVOR: "note"}


def test_gulp_is_error_sip_is_warning_savor_is_note() -> None:
    results = _sarif()["runs"][0]["results"]
    levels = {
        r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r["level"]
        for r in results
    }
    assert levels["auth/login.py"] == "error"
    assert levels[".github/workflows/ci.yml"] == "warning"
    assert levels["README.md"] == "note"


# --- physical locations ---------------------------------------------------


def test_physical_location_has_file_and_post_image_line_range() -> None:
    scored = _scored()
    by_uri = {
        r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r
        for r in _sarif()["runs"][0]["results"]
    }
    for s in scored:
        region = by_uri[s.hunk.file_path]["locations"][0]["physicalLocation"]["region"]
        assert region["startLine"] == s.hunk.new_start
        # endLine spans the declared new-file range and is never before start.
        assert region["endLine"] == s.hunk.new_start + s.hunk.new_lines - 1
        assert region["endLine"] >= region["startLine"]


def test_pure_deletion_collapses_to_a_single_line_region() -> None:
    # A hunk whose new side has zero lines (a pure deletion) must still land on a
    # valid 1-based single-line region, not a 0-length or inverted one.
    diff = "\n".join(
        [
            "diff --git a/mod.py b/mod.py",
            "--- a/mod.py",
            "+++ b/mod.py",
            "@@ -10,3 +9,0 @@",
            "-def gone():",
            "-    return 1",
            "-",
            "",
        ]
    )
    region = _sarif(diff)["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] >= 1
    assert region["endLine"] == region["startLine"]


# --- messages -------------------------------------------------------------


def test_message_text_is_the_one_line_why() -> None:
    results = _sarif()["runs"][0]["results"]
    by_uri = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in results}
    # The gulp result's message carries the danger reasons the human menu shows.
    auth_msg = by_uri["auth/login.py"]["message"]["text"]
    assert "eval/exec" in auth_msg
    assert "authentication/session" in auth_msg
    # The sip result names the CI surface.
    assert "CI" in by_uri[".github/workflows/ci.yml"]["message"]["text"]


def test_skim_safe_hunk_gets_a_friendly_message_and_synthetic_rule() -> None:
    results = _sarif()["runs"][0]["results"]
    savor = next(r for r in results if r["level"] == "note")
    assert r"skim-safe".lower() in savor["message"]["text"].lower()
    assert savor["ruleId"] == SKIM_RULE_ID


# --- rule catalog ---------------------------------------------------------


def test_every_result_ruleid_resolves_in_the_driver_catalog() -> None:
    run = _sarif()["runs"][0]
    catalog_ids = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
    for r in run["results"]:
        assert r["ruleId"] in catalog_ids


def test_result_ruleid_is_the_dominant_firing_rule() -> None:
    # The auth hunk's top signal is a danger signal, so its result.ruleId is
    # "danger" (the highest-impact firing rule), not merely the first alphabetically.
    results = _sarif()["runs"][0]["results"]
    auth = next(
        r
        for r in results
        if r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "auth/login.py"
    )
    assert auth["ruleId"] == "danger"


def test_rule_catalog_is_sorted_and_has_descriptions() -> None:
    rules = _sarif()["runs"][0]["tool"]["driver"]["rules"]
    ids = [rule["id"] for rule in rules]
    assert ids == sorted(ids)  # deterministic ordering
    for rule in rules:
        assert rule["shortDescription"]["text"]  # non-empty description


# --- properties -----------------------------------------------------------


def test_result_properties_carry_score_tier_and_hunk_id() -> None:
    scored = _scored()
    by_id = {s.hunk.id: s for s in scored}
    for r in _sarif()["runs"][0]["results"]:
        props = r["properties"]
        s = by_id[props["hunkId"]]
        assert props["score"] == s.score
        assert props["tier"] == tier_for(s.score).key


def test_title_and_fail_over_are_recorded_in_run_properties() -> None:
    log = _sarif(title="My PR #42", fail_over=60)
    props = log["runs"][0]["properties"]
    assert props["title"] == "My PR #42"
    assert props["failOver"] == 60


def test_no_run_properties_block_when_title_and_fail_over_omitted() -> None:
    run = _sarif()["runs"][0]
    assert "properties" not in run


# --- opt-in model notes (zero-point signals) ------------------------------


def test_zero_point_signals_do_not_become_a_phantom_rule() -> None:
    # A zero-point signal (like an --explain-llm "model:" note) explains but must
    # never decide the ruleId or leak into the catalog as a firing rule. We
    # simulate one by appending it to a scored hunk and re-rendering.
    from dataclasses import replace

    from diff_sommelier.rules import Signal

    scored = _scored()
    top = scored[0]
    note = Signal(rule="llm", points=0, reason="model: risky")
    noted = replace(top, signals=[*top.signals, note])
    rest = scored[1:]
    log = json.loads(render_sarif([noted, *rest]))
    run = log["runs"][0]
    catalog_ids = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
    assert "llm" not in catalog_ids
    # But the note text still rides along in the message.
    assert "model: risky" in run["results"][0]["message"]["text"]
    # And the ruleId is still the dominant *scoring* rule.
    assert run["results"][0]["ruleId"] == "danger"


# --- determinism ----------------------------------------------------------


def test_output_is_deterministic_across_runs() -> None:
    # No timestamps, stable ordering: identical input -> byte-identical output.
    a = render_sarif(_scored(), title="X", fail_over=50)
    b = render_sarif(_scored(), title="X", fail_over=50)
    assert a == b


def test_facade_matches_direct_renderer() -> None:
    # The render/__init__ facade forwards faithfully to the implementation.
    direct = render_sarif(_scored(), title="T", fail_over=30)
    facade = render_sarif_facade(_scored(), title="T", fail_over=30)
    assert direct == facade


def test_indent_none_produces_compact_single_line_json() -> None:
    compact = render_sarif(_scored(), indent=None)
    assert "\n" not in compact
    assert json.loads(compact)["version"] == "2.1.0"
