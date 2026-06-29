"""Plain-text "tasting menu" renderer (M4).

A deterministic, dependency-free ranked view of scored hunks. It is the
fallback whenever :mod:`rich` is unavailable, output is being piped, or the
user passes ``--no-color`` — and because it's deterministic, it's what the
snapshot tests pin.

Layout (one row per hunk, most-risky-first)::

    🍷 diff-sommelier — 3 hunks across 2 files · top risk 92

       #  TIER  SCR  RISK                    WHY
    ────────────────────────────────────────────────────────────────
       1  GULP   92  [################    ]  auth/login.py:1  adds ...
       2  SIP    34  [#######             ]  db/migrate.py:12  ...
       3  SAVR    0  [                    ]  README.md:1  (skim-safe)

Each row carries its risk **tier** (savor / sip / gulp), the 0-100 score, an
ASCII score bar, and the **why** — the hunk's ``file:line`` followed by the
reasons the rules emitted, most-impactful first. Long "why" cells wrap under a
hanging indent. A trailing legend reminds the reader what the tiers mean.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence

from diff_sommelier.render.tiers import GULP_AT, SIP_AT, tier_for
from diff_sommelier.scorer import ScoredHunk

__all__ = ["render_text", "BAR_WIDTH"]

# Width of the ASCII score bar, in cells. The score maps linearly onto it.
BAR_WIDTH = 20

# Default total line width used to budget the (wrapping) "why" column when the
# caller doesn't pin one. Kept modest so output is readable in a narrow pane.
DEFAULT_WIDTH = 100


def _bar(score: int) -> str:
    """Render a fixed-width ASCII bar for ``score`` (0-100)."""
    filled = round(score / 100 * BAR_WIDTH)
    filled = max(0, min(BAR_WIDTH, filled))
    return "[" + "#" * filled + " " * (BAR_WIDTH - filled) + "]"


def _location(hunk) -> str:
    """``file:line`` for a hunk, using the new-file start line."""
    return f"{hunk.file_path}:{hunk.new_start}"


def _why(scored: ScoredHunk) -> str:
    """The reasons line: each reason with its points, most-impactful first.

    Signals already arrive sorted by descending points from the scorer, so we
    keep that order. Zero-signal hunks get an explicit, honest placeholder
    rather than a blank cell.
    """
    if not scored.signals:
        return "(no notable signals — skim-safe)"
    return "; ".join(f"{s.reason} (+{s.points})" for s in scored.signals)


def _summary(scored: Sequence[ScoredHunk]) -> str:
    """Build the header line: hunk count, file count, and top risk score."""
    n_hunks = len(scored)
    n_files = len({s.hunk.file_path for s in scored})
    top = max((s.score for s in scored), default=0)
    hunk_word = "hunk" if n_hunks == 1 else "hunks"
    file_word = "file" if n_files == 1 else "files"
    return f"diff-sommelier — {n_hunks} {hunk_word} across {n_files} {file_word} · top risk {top}"


def render_text(scored: Sequence[ScoredHunk], *, width: int | None = None) -> str:
    """Render the ranked plain-text tasting menu for ``scored`` hunks.

    ``scored`` is expected most-risky-first (as :func:`score_diff` returns).
    The output is newline-joined and ends without a trailing newline so the
    caller controls final spacing.
    """
    total_width = DEFAULT_WIDTH if width is None else max(40, width)

    if not scored:
        return "diff-sommelier — nothing to taste (0 hunks). Pipe a diff to get a menu."

    rows = list(scored)
    idx_w = max(2, len(str(len(rows))))
    tier_w = 4  # GULP / SIP_ / SAVR labels are padded to 4 in Tier.label
    score_w = 3
    bracketed_bar_w = BAR_WIDTH + 2  # includes the [ ] brackets

    # The left gutter before the wrapping "why" cell:
    #   2 spaces, index, 2 spaces, tier, 2 spaces, score, 2 spaces, bar, 2 spaces
    gutter_w = 2 + idx_w + 2 + tier_w + 2 + score_w + 2 + bracketed_bar_w + 2
    why_width = max(24, total_width - gutter_w)

    lines: list[str] = []
    lines.append(f"🍷 {_summary(rows)}")
    lines.append("")

    header = (
        f"{'':2}{'#':>{idx_w}}  {'TIER':<{tier_w}}  {'SCR':>{score_w}}  "
        f"{'RISK':<{bracketed_bar_w}}  WHY"
    )
    lines.append(header)
    lines.append("─" * min(120, gutter_w + why_width))

    for i, s in enumerate(rows, start=1):
        tier = tier_for(s.score)
        why_cell = f"{_location(s.hunk)}  {_why(s)}"
        wrapped = textwrap.wrap(
            why_cell,
            width=why_width,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        gutter = (
            f"{'':2}{i:>{idx_w}}  {tier.label:<{tier_w}}  {s.score:>{score_w}}  {_bar(s.score)}  "
        )
        lines.append(gutter + wrapped[0])
        cont_indent = " " * gutter_w
        lines.extend(cont_indent + part for part in wrapped[1:])

    lines.append("")
    lines.append(
        f"Tiers: GULP (read first, ≥{GULP_AT}) · SIP (read, ≥{SIP_AT}) · "
        f"SAVR (skim-safe, <{SIP_AT}).  Listed most-risky-first."
    )
    return "\n".join(lines)
