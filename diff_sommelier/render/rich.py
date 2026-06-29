"""Rich colour "tasting menu" renderer (M4).

The same ranked layout as :mod:`diff_sommelier.render.text`, but rendered with
:mod:`rich`: a real table, colour-coded by risk tier, with a coloured score
bar per hunk. This is the default when output is an interactive terminal and
``rich`` is importable; otherwise the CLI uses the plain renderer.

Colour is the *only* thing this adds — the information (rank, tier, score,
``file:line``, why) is identical to the plain view, so nothing is lost when a
reader falls back to plain text. Importing this module requires ``rich``; the
package-level :func:`diff_sommelier.render.render_human` handles the missing
dependency by catching :class:`ImportError`.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from diff_sommelier.budget import BudgetResult, format_duration
from diff_sommelier.render.text import BAR_WIDTH, _location, _summary, _why
from diff_sommelier.render.tiers import GULP_AT, SIP_AT, tier_for
from diff_sommelier.scorer import ScoredHunk

__all__ = ["render_rich"]


def _bar_text(score: int, style: str) -> Text:
    """A coloured score bar: filled cells in the tier style, rest dim."""
    filled = max(0, min(BAR_WIDTH, round(score / 100 * BAR_WIDTH)))
    bar = Text()
    bar.append("█" * filled, style=style)
    bar.append("░" * (BAR_WIDTH - filled), style="dim")
    return bar


def _budget_spec(result: BudgetResult) -> str:
    """Short budget descriptor for the cut-line row."""
    if result.budget.is_count:
        return f"budget {result.cut} of {result.total} hunks"
    return (
        f"budget {format_duration(result.budget.seconds or 0.0)}"
        f" · ≈{format_duration(result.spent_seconds)} above"
    )


def render_rich(
    scored: Sequence[ScoredHunk],
    *,
    width: int | None = None,
    budget: BudgetResult | None = None,
) -> str:
    """Render the ranked tasting menu with rich and return it as a string.

    The output is captured to a string (rather than printed) so the CLI owns
    I/O and the renderer stays testable. ``width`` pins the console width when
    provided, which also makes the captured output reproducible. When
    ``budget`` is supplied (the M5 ``--budget`` cut), a coloured cut-line row
    is inserted after the last hunk that fits the budget.
    """
    rows = list(scored)
    console = Console(
        width=width,
        # Force styled output even when capturing to a buffer, so colours are
        # present; the CLI only selects this path for real terminals / when the
        # user asked for colour.
        force_terminal=True,
        color_system="standard",
        highlight=False,
    )

    if not rows:
        with console.capture() as cap:
            console.print(
                "🍷 [bold]diff-sommelier[/bold] — nothing to taste (0 hunks). "
                "Pipe a diff to get a menu."
            )
        return cap.get().rstrip("\n")

    table = Table(
        title=f"🍷 {_summary(rows)}",
        title_justify="left",
        expand=False,
        pad_edge=False,
        show_edge=False,
        header_style="bold",
    )
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("TIER", no_wrap=True)
    table.add_column("SCR", justify="right", no_wrap=True)
    table.add_column("RISK", no_wrap=True)
    table.add_column("WHY", overflow="fold")

    cut_after = budget.cut if (budget is not None and 0 < budget.cut < len(rows)) else None

    for i, s in enumerate(rows, start=1):
        tier = tier_for(s.score)
        why = Text()
        why.append(_location(s.hunk), style="bold")
        why.append("  ")
        why.append(_why(s))
        table.add_row(
            str(i),
            Text(tier.label, style=tier.style),
            Text(str(s.score), style=tier.style),
            _bar_text(s.score, tier.style),
            why,
        )
        if cut_after is not None and i == cut_after:
            label = Text(
                f"review {budget.reviewed} above · skim {budget.skimmed} below · "
                f"{_budget_spec(budget)}",
                style="cyan",
            )
            table.add_row(
                Text("──", style="bold cyan"),
                Text("CUT", style="bold cyan"),
                Text("───", style="cyan"),
                Text("─" * BAR_WIDTH, style="cyan"),
                label,
            )

    legend = (
        f"Tiers: [bold red]GULP[/bold red] (read first, ≥{GULP_AT}) · "
        f"[yellow]SIP[/yellow] (read, ≥{SIP_AT}) · "
        f"[green]SAVR[/green] (skim-safe, <{SIP_AT}).  Listed most-risky-first."
    )

    with console.capture() as cap:
        console.print(table)
        console.print(legend)
    return cap.get().rstrip("\n")
