"""Presenters for scored hunks (M4+).

The scorer (M3) produces a list of :class:`~diff_sommelier.scorer.ScoredHunk`
objects, most-risky-first. This package turns that list into something a
consumer can actually read:

* :mod:`~diff_sommelier.render.json` — the machine contract (a JSON array),
  unchanged from the M3 ``--json`` behaviour but now living with its siblings.
* :mod:`~diff_sommelier.render.text` — a deterministic, dependency-free
  plain-text "tasting menu". This is the fallback whenever ``rich`` is missing,
  output is redirected, or ``--no-color`` is passed, and it's what the snapshot
  tests pin.
* :mod:`~diff_sommelier.render.rich` — the colour terminal view: score bars and
  risk-tier colouring on top of the same layout. Degrades to the plain renderer
  if ``rich`` can't be imported.
* :mod:`~diff_sommelier.render.markdown` — a GitHub-flavoured Markdown "menu"
  sized for a PR comment (used by the GitHub Action): a reading-order checklist
  with skim-safe (or below-budget) hunks tucked into a collapsed ``<details>``
  block, score bars and optional source links.
* :mod:`~diff_sommelier.render.html` — a portable, self-contained HTML menu
  with score bars, an optional budget cut and source links.
* :mod:`~diff_sommelier.render.sarif` — a SARIF 2.1.0 log so the ranked hunks
  can be uploaded via ``upload-sarif`` and appear as inline code-scanning
  annotations in the PR "Files changed" view, the Security tab, and SARIF-aware
  IDEs. The risk tier drives the SARIF ``level`` (gulp→error, sip→warning,
  savor→note), so the annotations agree with the human menu.
* :mod:`~diff_sommelier.render.bundle` — a token-bounded, paste-ready review
  *bundle* for AI reviewers: the highest-risk hunks, most-dangerous-first,
  packed into a prompt that fits a context window (the machine-side sibling of
  the human ``--budget`` cut line). Builds the prompt only; no network/LLM call.

All three share the **risk tier** vocabulary (:class:`Tier`): every hunk is a
*savor* (skim-safe), a *sip* (read it), or a *gulp* (read this first). The tier
is a pure function of the 0-100 score so the human view, the colours, and any
later summary all agree.
"""

from __future__ import annotations

from diff_sommelier.budget import BudgetResult
from diff_sommelier.render.tiers import Tier, tier_for

__all__ = [
    "Tier",
    "tier_for",
    "render_human",
    "render_json",
    "render_markdown",
    "render_html",
    "render_sarif",
    "render_bundle",
]


def render_json(scored, *, indent: int | None = 2) -> str:
    """Render scored hunks as the canonical JSON array (see :mod:`.json`)."""
    from diff_sommelier.render.json import render_json as _impl

    return _impl(scored, indent=indent)


def render_markdown(
    scored,
    *,
    title: str | None = None,
    fail_over: int | None = None,
    budget: BudgetResult | None = None,
    repo_url: str | None = None,
) -> str:
    """Render the PR-comment Markdown menu (see :mod:`.markdown`)."""
    from diff_sommelier.render.markdown import render_markdown as _impl

    return _impl(scored, title=title, fail_over=fail_over, budget=budget, repo_url=repo_url)


def render_html(
    scored,
    *,
    title: str | None = None,
    fail_over: int | None = None,
    budget: BudgetResult | None = None,
    repo_url: str | None = None,
) -> str:
    """Render a self-contained HTML report (see :mod:`.html`)."""
    from diff_sommelier.render.html import render_html as _impl

    return _impl(scored, title=title, fail_over=fail_over, budget=budget, repo_url=repo_url)


def render_sarif(
    scored, *, title: str | None = None, fail_over: int | None = None, indent: int | None = 2
) -> str:
    """Render scored hunks as a SARIF 2.1.0 log (see :mod:`.sarif`)."""
    from diff_sommelier.render.sarif import render_sarif as _impl

    return _impl(scored, title=title, fail_over=fail_over, indent=indent)


def render_bundle(scored, *, budget, title: str | None = None) -> str:
    """Render a token-bounded AI-reviewer bundle (see :mod:`.bundle`).

    ``budget`` is a :class:`~diff_sommelier.render.bundle.ContextBudget` (parse
    a ``--context-budget`` spec with
    :func:`~diff_sommelier.render.bundle.parse_context_budget`).
    """
    from diff_sommelier.render.bundle import render_bundle as _impl

    return _impl(scored, budget=budget, title=title)


def render_human(scored, *, color: bool = True, width: int | None = None, budget=None) -> str:
    """Render the human "tasting menu".

    Uses the :mod:`rich` renderer when ``color`` is requested and ``rich`` is
    importable; otherwise falls back to the deterministic plain-text renderer.
    Returning a string (rather than printing) keeps rendering testable and lets
    the CLI own all the I/O. ``budget`` (a
    :class:`~diff_sommelier.budget.BudgetResult`), when given, draws the M5 cut
    line between "review this" and "skim this".
    """
    if color:
        try:
            from diff_sommelier.render.rich import render_rich

            return render_rich(scored, width=width, budget=budget)
        except ImportError:
            pass
    from diff_sommelier.render.text import render_text

    return render_text(scored, width=width, budget=budget)
