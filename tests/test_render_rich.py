"""Tests for the rich colour renderer and the human-render dispatch.

The rich output isn't pinned byte-for-byte (it depends on the installed
``rich`` version), so these assert the essentials: that it produces a styled,
ranked menu carrying the same information as the plain view, that an empty diff
is handled, and that :func:`render_human` falls back to plain text when colour
is off (or ``rich`` is unavailable).
"""

from __future__ import annotations

import builtins

from diff_sommelier.parser import parse_diff
from diff_sommelier.render import render_human
from diff_sommelier.render.text import render_text
from diff_sommelier.scorer import score_diff
from tests.test_render_text import MENU_DIFF


def _scored():
    return score_diff(parse_diff(MENU_DIFF))


def test_rich_output_is_styled_and_carries_the_data() -> None:
    from diff_sommelier.render.rich import render_rich

    out = render_rich(_scored(), width=100)
    # ANSI escape present -> colour was emitted.
    assert "\x1b[" in out
    # Same skeleton as the plain view: summary, locations, tiers, legend.
    assert "diff-sommelier" in out
    assert "auth/login.py:1" in out
    assert "GULP" in out and "SAVR" in out
    assert "most-risky-first" in out


def test_rich_empty_diff_is_handled() -> None:
    from diff_sommelier.render.rich import render_rich

    out = render_rich([], width=100)
    assert "0 hunks" in out


def test_render_human_without_color_matches_plain_text() -> None:
    scored = _scored()
    assert render_human(scored, color=False, width=100) == render_text(scored, width=100)


def test_render_human_falls_back_to_plain_when_rich_missing(monkeypatch) -> None:
    """If importing rich fails, render_human degrades to the plain renderer."""
    scored = _scored()
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "diff_sommelier.render.rich" or name == "rich":
            raise ImportError("simulated missing rich")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = render_human(scored, color=True, width=100)
    # Plain text has no ANSI escapes.
    assert "\x1b[" not in out
    assert out == render_text(scored, width=100)


def test_render_human_color_path_produces_ansi() -> None:
    out = render_human(_scored(), color=True, width=100)
    assert "\x1b[" in out
