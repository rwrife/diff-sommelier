"""Markdown "tasting menu" renderer (backlog #5 — GitHub Action).

A GitHub-flavoured Markdown view of scored hunks, sized for a **PR comment**.
It renders the same ranked, most-risky-first *tasting menu* as the terminal
views, but as a review-order checklist a human can act on inline:

* a one-line summary (hunk/file counts, top risk),
* a **checklist table** of the hunks worth a real read (the *gulp* and *sip*
  tiers), in reading order, each with its tier, score, ``file:line``, and the
  one-line *why*,
* the skim-safe *savor* hunks tucked into a collapsed ``<details>`` block so
  the comment stays short but nothing is hidden,
* an optional ``--fail-over`` threshold note when the caller is running a CI
  gate.

The output opens with a hidden HTML marker comment
(:data:`COMMENT_MARKER`) so the companion GitHub Action can find and *update*
its previous comment instead of posting a new one on every push (no comment
spam). Like the other presenters this returns a string and does no I/O, so it
stays trivially testable and the CLI/Action own delivery.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from diff_sommelier.budget import BudgetResult
from diff_sommelier.render.links import source_link, validate_repo_url
from diff_sommelier.render.text import _bar, _cut_label
from diff_sommelier.render.tiers import GULP_AT, SIP_AT, Tier, tier_for
from diff_sommelier.scorer import ScoredHunk

__all__ = ["render_markdown", "COMMENT_MARKER"]

# Hidden marker that lets the GitHub Action locate the comment it owns and edit
# it in place (idempotent, one comment per PR). It is an HTML comment, so it is
# invisible in the rendered PR view but survives in the raw comment body.
COMMENT_MARKER = "<!-- diff-sommelier:review-menu -->"

# A tasteful emoji per tier for the Markdown table (the terminal views use
# ASCII glyphs; a PR comment can afford colour).
_TIER_EMOJI = {
    Tier.GULP: "🔴",
    Tier.SIP: "🟡",
    Tier.SAVOR: "🟢",
}


def _location(hunk) -> str:
    """``file:line`` for a hunk, using the new-file start line."""
    return f"{hunk.file_path}:{hunk.new_start}"


def _escape_cell(text: str) -> str:
    """Make ``text`` safe for a single GitHub Markdown table cell.

    Neutralise HTML and Markdown markup, table delimiters and line breaks.
    """
    text = escape(text).replace("\r", " ").replace("\n", " ")
    for char in "\\`*_[]":
        text = text.replace(char, "\\" + char)
    return text.replace("|", "\\|")


def _reason_cell(signal) -> str:
    """Format one signal for the WHY column.

    A scoring signal shows its points (``reason (+7)``). A zero-point signal is
    a non-scoring **note** (e.g. an opt-in model note): the ``(+0)`` would be
    misleading, so we render just its text — mirroring the text renderer.
    """
    if signal.points > 0:
        return f"{signal.reason} (+{signal.points})"
    return signal.reason


def _why(scored: ScoredHunk) -> str:
    """The reasons for a hunk, most-impactful first, as one escaped cell."""
    if not scored.signals:
        return "_no notable signals — skim-safe_"
    return _escape_cell("; ".join(_reason_cell(s) for s in scored.signals))


def _summary(scored: Sequence[ScoredHunk]) -> str:
    """The header line: hunk count, file count, and the top risk score."""
    n_hunks = len(scored)
    n_files = len({s.hunk.file_path for s in scored})
    top = max((s.score for s in scored), default=0)
    hunk_word = "hunk" if n_hunks == 1 else "hunks"
    file_word = "file" if n_files == 1 else "files"
    return f"**{n_hunks} {hunk_word}** across **{n_files} {file_word}** · top risk **{top}**"


def _table(rows: Sequence[ScoredHunk], *, start: int = 1, repo_url: str | None = None) -> list[str]:
    """Render a Markdown checklist table for ``rows`` (already ranked)."""
    lines = [
        "| | # | Tier | Score | Location | Why |",
        "|---|---:|---|---:|---|---|",
    ]
    for i, s in enumerate(rows, start=start):
        tier = tier_for(s.score)
        emoji = _TIER_EMOJI[tier]
        loc = f"<code>{_escape_cell(_location(s.hunk))}</code>"
        if repo_url is not None:
            url = source_link(repo_url, s.hunk.file_path, s.hunk.new_start)
            loc = f"[{loc}]({url})"
        score = f"{s.score} <code>{_bar(s.score)}</code>"
        lines.append(f"| [ ] | {i} | {emoji} {tier.value.name} | {score} | {loc} | {_why(s)} |")
    return lines


def render_markdown(
    scored: Sequence[ScoredHunk],
    *,
    title: str | None = None,
    fail_over: int | None = None,
    budget: BudgetResult | None = None,
    repo_url: str | None = None,
) -> str:
    """Render the ranked tasting menu as a PR-comment Markdown string.

    ``scored`` is expected most-risky-first (as :func:`score_diff` returns).
    The *gulp* and *sip* hunks become a visible reading-order checklist; the
    skim-safe *savor* hunks go in a collapsed ``<details>`` block. ``title``
    overrides the default heading (e.g. to name the PR). ``fail_over``, when
    given, adds a CI-gate note and, if any hunk meets the threshold, a bold
    warning line.

    The returned string always starts with :data:`COMMENT_MARKER` so the
    companion Action can update its comment in place rather than spam new ones.
    With ``budget``, the cut replaces the default tier-based partition; hunks
    below it collapse regardless of tier. ``repo_url`` links each location to
    a source-view base that includes the revision.
    """
    if repo_url is not None:
        validate_repo_url(repo_url)
    heading = _escape_cell(title) if title else "🍷 diff-sommelier — review-order menu"

    lines: list[str] = [COMMENT_MARKER, f"## {heading}", ""]

    if not scored:
        lines.append("_Nothing to taste — no hunks in this diff._ 🥂")
        return "\n".join(lines)

    rows = list(scored)
    lines.append(_summary(rows))
    lines.append("")

    # Split into "read this" (gulp + sip) and "skim-safe" (savor). The list is
    # already ranked, so a simple partition preserves reading order in each.
    review = [s for s in rows if tier_for(s.score) is not Tier.SAVOR]
    skim = [s for s in rows if tier_for(s.score) is Tier.SAVOR]
    if budget is not None:
        review, skim = rows[: budget.cut], rows[budget.cut :]

    if review:
        lines.append("### Read these first")
        lines.append("")
        lines.append("_In recommended reading order, most-risky-first._")
        lines.append("")
        lines.extend(_table(review, repo_url=repo_url))
        lines.append("")
    else:
        lines.append("### Read these first")
        lines.append("")
        lines.append("_Nothing rises above skim level — the whole diff is low-risk._ 🎉")
        lines.append("")

    if budget is not None:
        lines.extend([f"> **{_cut_label(budget, 0).strip('─ ')}**", ""])

    if skim:
        lines.append("<details>")
        n = len(skim)
        hunk_word = "hunk" if n == 1 else "hunks"
        label = "Below budget cut" if budget is not None else "🟢 Skim-safe"
        suffix = "" if budget is not None else " (low risk)"
        lines.append(f"<summary>{label} · {n} {hunk_word}{suffix}</summary>")
        lines.append("")
        lines.extend(_table(skim, start=len(review) + 1, repo_url=repo_url))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if fail_over is not None:
        worst = max((s.score for s in rows), default=0)
        if worst >= fail_over:
            lines.append(
                f"> ⛔ **CI gate:** a hunk scored **{worst}** (≥ `{fail_over}`). "
                "Make sure it gets a real read before merging."
            )
        else:
            lines.append(
                f"> ✅ **CI gate:** top risk **{worst}** is under the `{fail_over}` threshold."
            )
        lines.append("")

    lines.append(
        f"<sub>Tiers: 🔴 gulp (read first, ≥{GULP_AT}) · 🟡 sip (read, ≥{SIP_AT}) · "
        f"🟢 savor (skim-safe, <{SIP_AT}). Ranked by risk + surprise · "
        "[diff-sommelier](https://github.com/rwrife/diff-sommelier)</sub>"
    )
    return "\n".join(lines)
