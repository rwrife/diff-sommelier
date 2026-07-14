"""Reviewer profiles (backlog #7, issue #30).

A backend reviewer and a frontend reviewer should not read the same diff in the
same order. ``--as <profile>`` applies a **multiplier map over surface
categories** so the ranking bends toward a reviewer's blind spots: ``backend``
up-weights migrations/auth/SQL and down-weights CSS/markup churn; ``frontend``
inverts it; ``security`` sharpens auth/crypto/secrets/deps.

The reweighting is **transparent**. When a profile changes a signal's points,
the reason line is annotated (e.g. ``… (boosted +30% by profile: backend)``),
so the "tasting menu" stays honest about *why* a hunk floated up or sank.

Design:

* Profiles operate on a signal's **category** — a stable, coarse label derived
  from the built-in surface reasons (``auth``, ``crypto``, ``migration``,
  ``ci``, ``container``, ``deps``, ``lockfile``, ``env``, ``frontend``) plus a
  ``frontend`` category inferred from the hunk's file path (CSS/markup/assets),
  which the surface rule doesn't emit a signal for but which a frontend reviewer
  very much cares about.
* A profile is a ``dict[category -> multiplier]``. Multiple profiles **compose**
  by multiplying their multipliers per category.
* Built-ins ship in :data:`BUILTIN_PROFILES`; custom profiles load from
  ``[profiles.<name>]`` tables in ``.sommelier.toml``. Unknown category names in
  a custom profile raise :class:`ProfileError` with the valid set listed.

The profile layer runs *after* scoring, rewriting :class:`ScoredHunk` objects,
because it needs to see each signal's category and re-normalize the summed
points. It never invents signals; it only scales existing ones (and can inject a
zero-point "frontend surface" marker so a path-based reweight is explainable).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from diff_sommelier.rules import Signal
from diff_sommelier.scorer import ScoredHunk, normalize

__all__ = [
    "ProfileError",
    "CATEGORIES",
    "BUILTIN_PROFILES",
    "signal_category",
    "compose",
    "resolve_profiles",
    "apply_profiles",
]


class ProfileError(RuntimeError):
    """A requested or configured reviewer profile could not be understood."""


# The coarse, stable categories a profile may reweight. These are the extension
# surface for reviewer profiles: a category groups related signals so a profile
# author reasons about "auth" or "frontend churn", not individual reasons.
CATEGORIES: tuple[str, ...] = (
    "auth",
    "crypto",
    "migration",
    "sql",
    "ci",
    "container",
    "deps",
    "lockfile",
    "env",
    "frontend",
)

# Map a built-in surface reason substring → its category. Kept as substrings so
# it stays robust if a reason is lightly reworded, and so config-added surfaces
# fall through to no category (unaffected by profiles) rather than misclassify.
_REASON_CATEGORY: tuple[tuple[str, str], ...] = (
    ("authentication/session", "auth"),
    ("cryptography/secrets", "crypto"),
    ("database migration/schema", "migration"),
    ("CI workflow", "ci"),
    ("CI/CD pipeline", "ci"),
    ("container/build config", "container"),
    ("declared dependencies", "deps"),
    ("dependency lockfile", "lockfile"),
    ("environment/credentials", "env"),
    # danger's SQL signal ("adds raw SQL") — a backend concern.
    ("raw sql", "sql"),
)

# Paths a *frontend* reviewer cares about but which emit no surface signal:
# stylesheets, markup, templates, and client asset bundles. A hunk touching one
# of these gets a zero-point "frontend surface" marker so a profile can reweight
# it explainably (and so `--as frontend` has something to boost).
_FRONTEND_PATH = re.compile(
    r"\.(css|scss|sass|less|styl|html?|htm|vue|svelte|jsx|tsx|astro)$"
    r"|(^|/)(components?|styles?|assets|public|templates?|views?|pages?)/",
    re.I,
)

_FRONTEND_MARKER_REASON = "touches frontend surface (markup/styles/components)"


# Built-in profiles: category → multiplier. 1.0 is neutral; >1 boosts, <1 sinks.
# Missing categories are implicitly 1.0. Values are deliberately moderate so a
# profile *bends* the order without steamrolling the raw heuristics.
BUILTIN_PROFILES: dict[str, dict[str, float]] = {
    "backend": {
        "migration": 1.4,
        "sql": 1.4,
        "auth": 1.3,
        "deps": 1.15,
        "frontend": 0.5,
    },
    "frontend": {
        "frontend": 1.6,
        "migration": 0.6,
        "sql": 0.6,
        "container": 0.7,
        "lockfile": 0.6,
    },
    "security": {
        "auth": 1.5,
        "crypto": 1.5,
        "env": 1.5,
        "deps": 1.3,
        "lockfile": 1.2,
    },
}


def signal_category(signal: Signal) -> str | None:
    """Return the coarse category for ``signal``, or ``None`` if it has none.

    Matching is case-insensitive substring over the reason (and, for the
    injected frontend marker, an exact reason match). Signals with no known
    category are untouched by profiles.
    """
    reason = signal.reason.lower()
    if signal.reason == _FRONTEND_MARKER_REASON:
        return "frontend"
    for needle, category in _REASON_CATEGORY:
        if needle.lower() in reason:
            return category
    return None


def compose(profiles: Iterable[Mapping[str, float]]) -> dict[str, float]:
    """Compose several profiles into one category→multiplier map (multiplying).

    A category present in more than one profile has its multipliers multiplied,
    so ``--as backend --as security`` stacks their boosts. Categories absent
    everywhere stay at the implicit ``1.0``.
    """
    out: dict[str, float] = {}
    for profile in profiles:
        for category, mult in profile.items():
            out[category] = out.get(category, 1.0) * float(mult)
    return out


def resolve_profiles(
    names: Iterable[str],
    custom: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, float]:
    """Resolve profile ``names`` into a single composed multiplier map.

    ``names`` may be built-ins or keys of ``custom`` (from ``.sommelier.toml``);
    a custom profile of the same name as a built-in shadows it. Unknown names
    raise :class:`ProfileError` listing what is available.
    """
    custom = dict(custom or {})
    available = {**BUILTIN_PROFILES, **custom}
    selected: list[Mapping[str, float]] = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        if name not in available:
            known = ", ".join(sorted(available))
            raise ProfileError(f"unknown profile '{name}' (available: {known}).")
        selected.append(available[name])
    return compose(selected)


def _describe(mult: float) -> str:
    """Human phrase for a multiplier, e.g. ``boosted +30%`` / ``eased -50%``."""
    pct = round((mult - 1.0) * 100)
    if pct > 0:
        return f"boosted +{pct}%"
    return f"eased {pct}%"


def _reweight_signals(
    signals: list[Signal],
    multipliers: Mapping[str, float],
    label: str,
) -> tuple[list[Signal], int]:
    """Return reweighted signals and their new summed raw points.

    Each signal whose category has a non-1.0 multiplier gets its points scaled
    (rounded, clamped at 0) and its reason annotated with the transparent
    ``(boosted +N% by profile: <label>)`` note. Signals that round to 0 points
    are dropped, matching the rule-runner's own zero-point filtering.
    """
    out: list[Signal] = []
    for sig in signals:
        category = signal_category(sig)
        mult = multipliers.get(category, 1.0) if category else 1.0
        if mult == 1.0:
            out.append(sig)
            continue
        points = max(0, round(sig.points * mult))
        if points <= 0:
            continue
        note = f"{sig.reason} ({_describe(mult)} by profile: {label})"
        out.append(Signal(rule=sig.rule, points=points, reason=note))
    raw = sum(s.points for s in out)
    return out, raw


def _frontend_marker(path: str) -> Signal | None:
    """A zero-point 'frontend surface' marker if ``path`` looks front-end.

    Injected before reweighting so a ``frontend`` multiplier has a signal to act
    on for CSS/markup hunks that no other rule flags. At neutral weight it stays
    zero points and is dropped, so the default (no-profile) path is unchanged.
    """
    if _FRONTEND_PATH.search(path.replace("\\", "/")):
        return Signal(rule="surface", points=8, reason=_FRONTEND_MARKER_REASON)
    return None


def apply_profiles(
    scored: Iterable[ScoredHunk],
    multipliers: Mapping[str, float],
    label: str,
) -> list[ScoredHunk]:
    """Re-rank ``scored`` under the composed profile ``multipliers``.

    For each hunk: optionally inject a frontend-surface marker (so path-based
    frontend reweighting is possible and explainable), scale each categorized
    signal by its multiplier, re-sum and re-normalize the score, then re-sort the
    whole set most-risky-first (same tiebreak as the scorer). Returns a fresh
    list; the input is not mutated. A no-op ``multipliers`` (all 1.0) returns the
    hunks unchanged in identity-preserving order.
    """
    if not multipliers or all(m == 1.0 for m in multipliers.values()):
        return list(scored)

    frontend_active = multipliers.get("frontend", 1.0) != 1.0
    out: list[ScoredHunk] = []
    for sh in scored:
        signals = list(sh.signals)
        if frontend_active:
            marker = _frontend_marker(sh.hunk.file_path)
            if marker is not None and not any(s.reason == _FRONTEND_MARKER_REASON for s in signals):
                signals.append(marker)
        new_signals, raw = _reweight_signals(signals, multipliers, label)
        new_signals.sort(key=lambda s: s.points, reverse=True)
        out.append(
            ScoredHunk(
                hunk=sh.hunk,
                score=normalize(raw),
                raw=raw,
                signals=new_signals,
            )
        )
    out.sort(key=lambda s: (-s.score, -s.raw, s.hunk.id))
    return out
