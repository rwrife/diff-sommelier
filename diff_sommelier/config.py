"""Configuration file support (M6).

A project can drop a ``.sommelier.toml`` at its root to tune scoring without
forking the tool. Two things are configurable, matching the M6 scope:

* ``[weights]`` — a per-rule multiplier applied to every point that rule emits.
  Keys are rule names (``size``, ``surface``, ``danger``); values are floats.
  ``1.0`` is the default (no change); ``0`` mutes a rule; ``2.0`` doubles its
  influence. The *reasons* are preserved verbatim, so the menu stays honest —
  only the points (and therefore the 0-100 score) move.

* ``[[surface]]`` — extra "dangerous by location" path patterns layered on top
  of the built-ins, so a team can mark, say, ``infra/terraform/`` or
  ``payments/`` as sensitive. Each entry is a table::

      [[surface]]
      pattern = "(^|/)payments/"   # Python regex, matched case-insensitively
      points  = 12
      reason  = "touches the payments module"

Discovery walks up from the working directory to the filesystem root (or a
git/project boundary), so running ``diff-sommelier`` anywhere inside a repo
finds the repo's config. ``--config PATH`` forces a specific file, and
``--no-config`` skips discovery entirely. Anything malformed raises
:class:`ConfigError` with a message pointing at the offending file.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import ALL_RULES, Rule, Signal
from diff_sommelier.rules import danger as _danger
from diff_sommelier.rules import size as _size
from diff_sommelier.rules import surface as _surface

# Rule name of the opt-in blast-radius rule. Imported as a constant (not the
# module) to keep this file's import graph light and avoid a cycle: the rule is
# assembled in the CLI, config only needs to know the *name* is weightable.
_BLAST_RADIUS_RULE = "blast-radius"

# Rule name of the opt-in hotspots rule (git-log churn). Same rationale as the
# blast-radius constant above: weightable by name without importing the module.
_HOTSPOTS_RULE = "hotspots"

# Likewise the opt-in owners rule (--owners): weightable by name without
# importing the module, keeping this file's import graph light.
_OWNERS_RULE = "owners"

# Rule name of the opt-in no-tests rule (--no-tests): weightable by name without
# importing the module, keeping this file's import graph light.
_NO_TESTS_RULE = "no-tests"

# Rule name of the opt-in intent rule (--intent): weightable by name.
_INTENT_RULE = "intent"

__all__ = [
    "ConfigError",
    "Config",
    "CONFIG_FILENAME",
    "find_config",
    "load_config",
]

CONFIG_FILENAME = ".sommelier.toml"

# Names the [weights] table may key on. These are the RULE constants the built-in
# rule modules report on their signals, so weighting keys match what shows in
# `--json` ("rule": ...). ``blast-radius`` is the opt-in rule (only active with
# --blast-radius) but is weightable here so a project can tune it uniformly.
_KNOWN_RULES = (
    _size.RULE,
    _surface.RULE,
    _danger.RULE,
    _BLAST_RADIUS_RULE,
    _HOTSPOTS_RULE,
    _OWNERS_RULE,
    _NO_TESTS_RULE,
    _INTENT_RULE,
)


class ConfigError(RuntimeError):
    """A ``.sommelier.toml`` was found but could not be understood."""


# An extra surface pattern parsed from config: (compiled regex, points, reason).
ExtraSurface = tuple[re.Pattern[str], int, str]


@dataclass(frozen=True)
class Config:
    """Parsed, validated tuning loaded from a ``.sommelier.toml``.

    ``weights`` maps a rule name to its multiplier (absent rules implicitly
    ``1.0``). ``extra_surface`` is the list of user-defined surface patterns.
    ``path`` is where it came from (``None`` for the built-in default), shown in
    error messages and handy for debugging.
    """

    weights: dict[str, float] = field(default_factory=dict)
    extra_surface: tuple[ExtraSurface, ...] = ()
    profiles: dict[str, dict[str, float]] = field(default_factory=dict)
    path: Path | None = None

    @property
    def is_default(self) -> bool:
        """True when nothing was customized (no file, or an empty file)."""
        return not self.weights and not self.extra_surface and not self.profiles

    def rules(self) -> list[Rule]:
        """Build the rule list that applies this config.

        The surface rule is swapped for one that also honours
        :attr:`extra_surface`; then every rule is wrapped so its emitted points
        are scaled by the configured weight (default ``1.0``). Weighting is
        applied as a post-filter on each rule's signals, so rule code never has
        to know about config and explanations are untouched.
        """
        surface_rule = _surface.make_rule(self.extra_surface)
        base: list[tuple[str, Rule]] = [
            (_size.RULE, _size.score),
            (_surface.RULE, surface_rule),
            (_danger.RULE, _danger.score),
        ]
        out: list[Rule] = []
        for name, rule in base:
            weight = self.weights.get(name, 1.0)
            out.append(_weighted(rule, weight))
        return out

    def apply_weight(self, name: str, rule: Rule) -> Rule:
        """Wrap an *externally built* ``rule`` with its configured weight.

        Used for the opt-in blast-radius rule, which the CLI assembles after
        :meth:`rules` (it needs a repo scan the config layer shouldn't own). This
        keeps all weighting — including for that rule — flowing through the same
        ``[weights]`` table and the same :func:`_weighted` wrapper.
        """
        return _weighted(rule, self.weights.get(name, 1.0))


def _weighted(rule: Rule, weight: float) -> Rule:
    """Wrap ``rule`` so each signal's points are multiplied by ``weight``.

    Points are re-rounded to ints and clamped at 0; a weight of exactly ``1.0``
    returns the rule unchanged so the default path has zero overhead. Signals
    that round down to 0 points are simply dropped downstream by
    :func:`diff_sommelier.rules.run_rules`.
    """
    if weight == 1.0:
        return rule

    def scaled(hunk: Hunk, file: File) -> Iterable[Signal]:
        for sig in rule(hunk, file):
            points = max(0, round(sig.points * weight))
            yield Signal(rule=sig.rule, points=points, reason=sig.reason)

    return scaled


def find_config(start: Path, *, filename: str = CONFIG_FILENAME) -> Path | None:
    """Search ``start`` and its parents for ``filename``; return it or ``None``.

    Walks upward to the filesystem root. The first match wins, so a config
    nearer the working directory shadows one higher up.
    """
    start = start.resolve()
    if start.is_dir():
        candidates = [start, *start.parents]
    else:
        candidates = [start.parent, *start.parent.parents]
    for directory in candidates:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def load_config(
    *,
    explicit: Path | None = None,
    start: Path | None = None,
    enabled: bool = True,
) -> Config:
    """Resolve and parse configuration.

    * ``enabled=False`` (the ``--no-config`` flag) → the built-in default,
      regardless of any file on disk.
    * ``explicit`` set (the ``--config PATH`` flag) → that exact file is parsed;
      a missing file is a :class:`ConfigError`.
    * otherwise discovery walks up from ``start`` (default: cwd); if nothing is
      found, the built-in default is returned.
    """
    if not enabled:
        return Config()
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        return _parse(explicit)
    found = find_config(start or Path.cwd())
    if found is None:
        return Config()
    return _parse(found)


def _parse(path: Path) -> Config:
    """Read and validate a ``.sommelier.toml`` into a :class:`Config`."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{path}: could not read config: {exc}") from exc

    weights = _parse_weights(path, data.get("weights", {}))
    extra = _parse_surface(path, data.get("surface", []))
    profiles = _parse_profiles(path, data.get("profiles", {}))
    return Config(weights=weights, extra_surface=extra, profiles=profiles, path=path)


def _parse_weights(path: Path, raw: object) -> dict[str, float]:
    """Validate the ``[weights]`` table: known rule names → non-negative floats."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [weights] must be a table of rule = number.")
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in _KNOWN_RULES:
            known = ", ".join(_KNOWN_RULES)
            raise ConfigError(f"{path}: unknown rule '{key}' in [weights] (known: {known}).")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path}: weight for '{key}' must be a number, got {value!r}.")
        if value < 0:
            raise ConfigError(f"{path}: weight for '{key}' must be >= 0, got {value}.")
        out[key] = float(value)
    return out


def _parse_surface(path: Path, raw: object) -> tuple[ExtraSurface, ...]:
    """Validate the ``[[surface]]`` array of {pattern, points, reason} tables."""
    if raw in ((), [], None):
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{path}: [[surface]] must be an array of tables.")
    out: list[ExtraSurface] = []
    for i, entry in enumerate(raw):
        where = f"{path}: surface entry #{i + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table with pattern/points/reason.")
        pattern = entry.get("pattern")
        points = entry.get("points")
        reason = entry.get("reason")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(f"{where}: 'pattern' must be a non-empty string.")
        if isinstance(points, bool) or not isinstance(points, int):
            raise ConfigError(f"{where}: 'points' must be an integer.")
        if points <= 0:
            raise ConfigError(f"{where}: 'points' must be a positive integer.")
        if not isinstance(reason, str) or not reason:
            raise ConfigError(f"{where}: 'reason' must be a non-empty string.")
        try:
            compiled = re.compile(pattern, re.I)
        except re.error as exc:
            raise ConfigError(f"{where}: invalid regex {pattern!r}: {exc}") from exc
        out.append((compiled, points, reason))
    return tuple(out)


def _parse_profiles(path: Path, raw: object) -> dict[str, dict[str, float]]:
    """Validate the ``[profiles.<name>]`` tables: category -> multiplier maps.

    Each key must be a known reviewer-profile category (see
    :data:`diff_sommelier.profiles.CATEGORIES`); each value a non-negative
    number. A profile named like a built-in shadows it at resolve time. An
    unknown category name errors clearly, listing the valid set.
    """
    if raw in ((), [], None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [profiles] must be a table of named profiles.")
    # Imported here (not at module top) to keep the import graph light and avoid
    # pulling the scorer/profile stack in for the common no-config path.
    from diff_sommelier.profiles import CATEGORIES

    out: dict[str, dict[str, float]] = {}
    for name, table in raw.items():
        where = f"{path}: profile '{name}'"
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table of category = multiplier.")
        mults: dict[str, float] = {}
        for category, value in table.items():
            if category not in CATEGORIES:
                known = ", ".join(CATEGORIES)
                raise ConfigError(f"{where}: unknown category '{category}' (known: {known}).")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(
                    f"{where}: multiplier for '{category}' must be a number, got {value!r}."
                )
            if value < 0:
                raise ConfigError(
                    f"{where}: multiplier for '{category}' must be >= 0, got {value}."
                )
            mults[category] = float(value)
        out[name] = mults
    return out


# Re-exported so callers that want the unconfigured behaviour have a single name
# rather than reaching into the rules package.
DEFAULT_RULES: list[Rule] = list(ALL_RULES)
