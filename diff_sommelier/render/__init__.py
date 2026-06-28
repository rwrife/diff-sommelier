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

All three share the **risk tier** vocabulary (:class:`Tier`): every hunk is a
*savor* (skim-safe), a *sip* (read it), or a *gulp* (read this first). The tier
is a pure function of the 0-100 score so the human view, the colours, and any
later summary all agree.
"""

from __future__ import annotations

from diff_sommelier.render.tiers import Tier, tier_for

__all__ = [
    "Tier",
    "tier_for",
    "render_human",
    "render_json",
]


def render_json(scored, *, indent: int | None = 2) -> str:
    """Render scored hunks as the canonical JSON array (see :mod:`.json`)."""
    from diff_sommelier.render.json import render_json as _impl

    return _impl(scored, indent=indent)


def render_human(scored, *, color: bool = True, width: int | None = None) -> str:
    """Render the human "tasting menu".

    Uses the :mod:`rich` renderer when ``color`` is requested and ``rich`` is
    importable; otherwise falls back to the deterministic plain-text renderer.
    Returning a string (rather than printing) keeps rendering testable and lets
    the CLI own all the I/O.
    """
    if color:
        try:
            from diff_sommelier.render.rich import render_rich

            return render_rich(scored, width=width)
        except ImportError:
            pass
    from diff_sommelier.render.text import render_text

    return render_text(scored, width=width)
