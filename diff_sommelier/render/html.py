"""Self-contained HTML tasting-menu reports; no scripts or external assets."""

from collections.abc import Sequence
from html import escape

from diff_sommelier.budget import BudgetResult
from diff_sommelier.render.links import source_link, validate_repo_url
from diff_sommelier.render.text import _cut_label, _summary, _why
from diff_sommelier.render.tiers import GULP_AT, SIP_AT, tier_for
from diff_sommelier.scorer import ScoredHunk

_CSS = """\
body { font: 16px system-ui, sans-serif; max-width: 70rem; margin: 2rem auto;
  padding: 0 1rem; color: #24292f; background: #fff; }
article { border: 1px solid #d0d7de; border-left: .4rem solid #57606a;
  border-radius: .4rem; margin: 1rem 0; padding: 1rem; overflow-wrap: anywhere; }
article.gulp { border-left-color: #cf222e; }
article.sip { border-left-color: #9a6700; }
article.savor { border-left-color: #1a7f37; }
h2 { font-size: 1.1rem; margin-top: 0; }
a { color: #0969da; }
.bar { display: inline-block; width: 10rem; height: .7rem; background: #eaeef2; }
.bar span { display: block; height: 100%; background: #57606a; }
.cut { border-top: 2px dashed #57606a; padding-top: 1rem; font-weight: bold; }
.skim { background: #f6f8fa; }
footer { margin-top: 2rem; color: #57606a; }
"""


def render_html(
    scored: Sequence[ScoredHunk],
    *,
    title: str | None = None,
    fail_over: int | None = None,
    budget: BudgetResult | None = None,
    repo_url: str | None = None,
) -> str:
    """Render ranked locations, reasons and an optional attention-budget cut."""
    if repo_url is not None:
        validate_repo_url(repo_url)
    heading = escape(title or "diff-sommelier - review-order menu")
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{heading}</title>",
        f"<style>\n{_CSS}</style>",
        "</head>",
        "<body>",
        "<main>",
        f"<h1>{heading}</h1>",
        f"<p>{escape(_summary(scored))}</p>",
    ]
    if not scored:
        lines.append("<p>Nothing to taste - no hunks in this diff.</p>")
    for index, row in enumerate(scored):
        if budget is not None and index == budget.cut:
            lines.append(f'<p class="cut">{escape(_cut_label(budget, 0).strip("─ "))}</p>')
        tier = tier_for(row.score).value.name
        skim = " skim" if budget is not None and index >= budget.cut else ""
        loc = f"<code>{escape(f'{row.hunk.file_path}:{row.hunk.new_start}')}</code>"
        if repo_url is not None:
            url = escape(source_link(repo_url, row.hunk.file_path, row.hunk.new_start), quote=True)
            loc = f'<a href="{url}">{loc}</a>'
        lines.extend(
            [
                f'<article class="{tier}{skim}">',
                f"<h2>#{index + 1} - {tier} - {loc}</h2>",
                f'<p>Risk {row.score}/100 <span class="bar" aria-hidden="true">'
                f'<span style="width: {row.score}%"></span></span></p>',
                f"<p>{escape(_why(row))}</p>",
                "</article>",
            ]
        )
    if budget is not None and budget.cut >= len(scored):
        lines.append(f'<p class="cut">{escape(_cut_label(budget, 0).strip("─ "))}</p>')
    if fail_over is not None and scored:
        worst = max(row.score for row in scored)
        status = "threshold met or exceeded" if worst >= fail_over else "under threshold"
        lines.append(f"<p><strong>CI gate: top risk {worst}; {status} ({fail_over}).</strong></p>")
    lines.extend(
        [
            "</main>",
            f"<footer>Tiers: gulp (read first, &gt;={GULP_AT}); sip (read, &gt;={SIP_AT}); "
            f"savor (skim-safe, &lt;{SIP_AT}). Ranked most-risky-first.</footer>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)
