"""SARIF 2.1.0 presenter (issue #24 — code-scanning annotations).

diff-sommelier already ranks hunks by risk and renders JSON / text / rich /
Markdown. This renderer emits the same ranked hunks as **SARIF 2.1.0** so they
can be uploaded with ``github/codeql-action/upload-sarif`` and surface as inline
annotations in the PR **"Files changed"** view, the repo **Security → Code
scanning** tab, and any SARIF-aware IDE (the VS Code SARIF Viewer, etc.).

This is *reviewer-side triage as a first-class code-scanning result*: instead of
a comment nobody expands, the "gulp" hunks become annotations exactly where the
reviewer is already looking. SARIF is just JSON, so there are no new heavy deps.

Shape (one ``run`` with one ``result`` per scored hunk):

* **Tier → level.** Each hunk's :class:`~diff_sommelier.render.tiers.Tier`
  (a pure function of the 0-100 score, shared with every other renderer) maps to
  a SARIF ``level``: ``gulp → error``, ``sip → warning``, ``savor → note``. So
  the SARIF severity always agrees with the human menu's colours.
* **Location.** ``physicalLocation`` is the hunk's file plus its **post-image**
  line range (``startLine``/``endLine`` from the parsed :class:`Hunk`), so the
  annotation lands on the changed lines in the new file.
* **Message.** ``message.text`` is the one-line "why" — the same reason string
  the human menu shows (the rules that fired, most-impactful first).
* **Rules catalog.** ``tool.driver.rules`` lists one entry per rule that fired
  anywhere in the diff (``size``, ``surface``, ``danger``, and any opt-in rule
  like ``blast-radius``/``hotspots``), so every ``result.ruleId`` resolves.
* **Properties.** Each result's ``properties`` bag carries the numeric 0-100
  ``score`` and the hunk's stable ``id``, so downstream tooling can sort/filter
  without re-deriving them.

The output is **deterministic** — stable ordering (the scorer's most-risky-first
order), sorted rule catalog, and no timestamps — so it snapshot-tests like the
other renderers. Like them it returns a string and does no I/O; the CLI owns
delivery.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from diff_sommelier.render.tiers import Tier, tier_for
from diff_sommelier.scorer import ScoredHunk

__all__ = ["render_sarif", "SARIF_VERSION", "SARIF_SCHEMA", "TIER_LEVEL"]

# SARIF interchange format version and the canonical schema URL for 2.1.0.
SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
)

# The tool identity stamped into every run's ``tool.driver``.
_DRIVER_NAME = "diff-sommelier"
_DRIVER_INFO_URI = "https://github.com/rwrife/diff-sommelier"

# Tier → SARIF result level. This is the whole point of the mapping: the same
# risk bucket the human menu colours drives the code-scanning severity, so a
# "gulp" is an ``error`` annotation, a "sip" a ``warning``, a "savor" a ``note``.
TIER_LEVEL: dict[Tier, str] = {
    Tier.GULP: "error",
    Tier.SIP: "warning",
    Tier.SAVOR: "note",
}

# Synthetic ruleId for a skim-safe hunk that produced no scoring signals (all
# rules stayed quiet). Every SARIF result needs a ``ruleId`` that resolves in
# the driver's rule catalog, so we give these a stable, self-describing one
# rather than omitting the field.
SKIM_RULE_ID = "skim-safe"

# Short, human-readable descriptions for the built-in rules, used to populate
# the rule catalog's ``shortDescription``. Opt-in / dynamically-added rules that
# aren't listed here still get a catalog entry (their id as the description), so
# nothing is ever left dangling.
_RULE_DESCRIPTIONS: dict[str, str] = {
    "size": "The hunk changes a large number of lines.",
    "surface": "The hunk touches a sensitive surface (auth, CI, config, infra, ...).",
    "danger": "The hunk contains a risky construct (eval/exec, secrets, a large deletion, ...).",
    "blast-radius": "The hunk changes a symbol used widely elsewhere in the repo.",
    "hotspots": "The hunk lands in a historically bug-prone file (high churn / fix frequency).",
    SKIM_RULE_ID: "No notable risk signals fired — this hunk is skim-safe.",
}


def _scoring_signals(scored: ScoredHunk) -> list:
    """The signals that actually moved the score (positive points only).

    Zero-point signals (e.g. the opt-in ``model:`` notes from ``--explain-llm``)
    explain but never change risk, so they must not decide a result's ``ruleId``
    or leak in as a phantom firing rule. They are still surfaced in the message
    text via :func:`_message_text`.
    """
    return [s for s in scored.signals if s.points > 0]


def _primary_rule_id(scored: ScoredHunk) -> str:
    """The ruleId for a hunk's result: its highest-impact firing rule.

    Signals are already sorted most-impactful-first by the scorer, so the first
    positive-point signal is the dominant reason. A hunk with no scoring signals
    is skim-safe and gets :data:`SKIM_RULE_ID`.
    """
    scoring = _scoring_signals(scored)
    if scoring:
        return scoring[0].rule
    return SKIM_RULE_ID


def _message_text(scored: ScoredHunk) -> str:
    """The one-line "why" for a hunk — the reasons, most-impactful first.

    Mirrors what the human menu shows (including any zero-point ``model:`` note),
    so the annotation reads identically to the terminal/Markdown views. A hunk
    with no signals at all gets a friendly skim-safe message.
    """
    reasons = scored.reasons
    if not reasons:
        return "No notable risk signals — skim-safe."
    return "; ".join(reasons)


def _region(hunk) -> dict:
    """The SARIF ``region`` for a hunk: its post-image (new-file) line range.

    ``startLine`` is the hunk's 1-based new-file start; ``endLine`` is the last
    line of its declared new span. A zero-line span (a pure deletion, where the
    new side has no lines) collapses to a single-line region at ``new_start`` so
    the annotation still has somewhere valid to land (SARIF lines are 1-based and
    ``endLine`` must be >= ``startLine``).
    """
    start = max(1, hunk.new_start)
    span = hunk.new_lines if hunk.new_lines > 0 else 1
    end = start + span - 1
    return {"startLine": start, "endLine": max(start, end)}


def _result(scored: ScoredHunk) -> dict:
    """Build one SARIF ``result`` object for a scored hunk."""
    tier = tier_for(scored.score)
    hunk = scored.hunk
    return {
        "ruleId": _primary_rule_id(scored),
        "level": TIER_LEVEL[tier],
        "message": {"text": _message_text(scored)},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": hunk.file_path},
                    "region": _region(hunk),
                }
            }
        ],
        "properties": {
            "score": scored.score,
            "tier": tier.key,
            "hunkId": hunk.id,
        },
    }


def _rules_catalog(scored: Sequence[ScoredHunk]) -> list[dict]:
    """Every ``ruleId`` that appears in the results, as a driver rule catalog.

    Collected from the results themselves (so it always covers exactly the
    ``ruleId`` values used, including :data:`SKIM_RULE_ID` and any opt-in rule),
    then sorted for deterministic output. Each entry carries a short description;
    rules without a curated blurb fall back to their id so nothing is blank.
    """
    ids = sorted({_primary_rule_id(s) for s in scored})
    catalog: list[dict] = []
    for rule_id in ids:
        catalog.append(
            {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": _RULE_DESCRIPTIONS.get(rule_id, rule_id)},
            }
        )
    return catalog


def render_sarif(
    scored: Iterable[ScoredHunk],
    *,
    title: str | None = None,
    fail_over: int | None = None,
    indent: int | None = 2,
) -> str:
    """Render scored hunks as a SARIF 2.1.0 JSON log string.

    ``scored`` is expected most-risky-first (as
    :func:`~diff_sommelier.scorer.score_diff` returns); that order is preserved
    in ``results`` so the log is deterministic. ``title``, when given, is
    recorded on the run's ``properties`` (a hint for consumers naming the PR).
    ``fail_over`` is likewise recorded as ``properties.failOver`` so the same
    CI-gate threshold the CLI enforces is discoverable in the log — the exit code
    is still owned by the CLI, this renderer only *describes*. ``indent`` mirrors
    the JSON renderer (2 by default; ``None`` for compact output).

    The result is a single-run SARIF log with no timestamps, so identical input
    yields byte-identical output and it snapshot-tests like the other renderers.
    """
    results_list = list(scored)

    driver: dict = {
        "name": _DRIVER_NAME,
        "informationUri": _DRIVER_INFO_URI,
        "rules": _rules_catalog(results_list),
    }
    # Include the tool version when it is importable. Kept lazy/defensive so the
    # renderer never hard-depends on package metadata being present.
    try:
        from diff_sommelier import __version__

        driver["version"] = __version__
    except Exception:  # pragma: no cover - version is always available in-tree
        pass

    run: dict = {
        "tool": {"driver": driver},
        "results": [_result(s) for s in results_list],
    }

    run_properties: dict = {}
    if title is not None:
        run_properties["title"] = title
    if fail_over is not None:
        run_properties["failOver"] = fail_over
    if run_properties:
        run["properties"] = run_properties

    log = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [run],
    }
    return json.dumps(log, indent=indent)
