"""Risk tiers: the shared savor / sip / gulp vocabulary (M4).

A hunk's 0-100 score is precise, but humans triage in buckets. Every presenter
collapses the score into one of three tiers so the wording, glyphs, and colours
stay consistent across the plain and rich views:

* **gulp** — high risk / high surprise. *Read this first.*
* **sip** — worth a real read, not an emergency.
* **savor** — skim-safe; low risk, enjoy at leisure.

The thresholds live here, in one place, so "what counts as a gulp" is a single
definition the whole tool (and later the ``--budget`` cut line) agrees on. The
tiers are derived purely from the score — no hidden state — so the same hunk is
the same tier in every renderer and every run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["Tier", "tier_for"]


@dataclass(frozen=True)
class _TierInfo:
    name: str
    label: str
    glyph: str
    # ``rich`` colour/style name for this tier; the plain renderer ignores it.
    style: str


class Tier(Enum):
    """A coarse risk bucket for a scored hunk.

    The value carries the display metadata (label, a plain-ASCII glyph, and a
    ``rich`` style) so presenters don't each re-invent the vocabulary.
    """

    GULP = _TierInfo("gulp", "GULP", "!!", "bold red")
    SIP = _TierInfo("sip", "SIP ", "~ ", "yellow")
    SAVOR = _TierInfo("savor", "SAVR", "  ", "green")

    @property
    def label(self) -> str:
        """Fixed-width tier label used in the menu's left gutter."""
        return self.value.label

    @property
    def glyph(self) -> str:
        """Short ASCII marker for the plain-text view."""
        return self.value.glyph

    @property
    def style(self) -> str:
        """``rich`` style name for the colour view."""
        return self.value.style

    @property
    def key(self) -> str:
        """Lowercase tier name (``"gulp"``/``"sip"``/``"savor"``)."""
        return self.value.name


# Inclusive lower bounds. A score >= GULP_AT is a gulp; >= SIP_AT is a sip;
# everything else is a savor. Tuned against the M3 normalization curve so that
# a hunk with a strong danger signal lands in "gulp", a single moderate
# surface/size signal lands in "sip", and trivial churn stays "savor".
GULP_AT = 60
SIP_AT = 25


def tier_for(score: int) -> Tier:
    """Return the :class:`Tier` for a 0-100 ``score``."""
    if score >= GULP_AT:
        return Tier.GULP
    if score >= SIP_AT:
        return Tier.SIP
    return Tier.SAVOR
