"""Tests for .sommelier.toml support (M6): discovery, weight scaling,
extra surface patterns, and validation errors. Scoring effects are checked
through the public scorer so we verify the *score* moves, not internals."""

from __future__ import annotations

from pathlib import Path

import pytest

from diff_sommelier.config import (
    Config,
    ConfigError,
    find_config,
    load_config,
)
from diff_sommelier.parser import parse_diff
from diff_sommelier.scorer import score_diff

# A small diff touching a path that is NOT a built-in sensitive surface, with a
# moderate size so the size rule fires (>=15 changed lines).
_BIG = "\n".join(
    [
        "diff --git a/svc/widget.py b/svc/widget.py",
        "--- a/svc/widget.py",
        "+++ b/svc/widget.py",
        "@@ -1,2 +1,20 @@",
        " keep = 0",
        *[f"+line{i} = {i}" for i in range(18)],
        "",
    ]
)


def _score(text: str, config: Config):
    diff = parse_diff(text.splitlines(keepends=True))
    return score_diff(diff, rules=config.rules())


def test_default_config_is_default() -> None:
    cfg = Config()
    assert cfg.is_default
    assert cfg.weights == {}
    assert cfg.extra_surface == ()


def test_no_config_flag_returns_default(tmp_path: Path) -> None:
    (tmp_path / ".sommelier.toml").write_text("[weights]\nsize = 0\n")
    cfg = load_config(enabled=False, start=tmp_path)
    assert cfg.is_default


def test_find_config_walks_up(tmp_path: Path) -> None:
    (tmp_path / ".sommelier.toml").write_text("")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = find_config(nested)
    assert found == tmp_path / ".sommelier.toml"


def test_find_config_missing_returns_none(tmp_path: Path) -> None:
    assert find_config(tmp_path) is None


def test_missing_explicit_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(explicit=tmp_path / "nope.toml")


def test_weight_zero_mutes_a_rule(tmp_path: Path) -> None:
    """size=0 removes the size signal, dropping the score to 0."""
    base = _score(_BIG, Config())
    assert base[0].score > 0
    cfg = Config(weights={"size": 0.0})
    muted = _score(_BIG, cfg)
    assert muted[0].score == 0
    assert not any(s.rule == "size" for s in muted[0].signals)


def test_weight_amplifies_a_rule(tmp_path: Path) -> None:
    """A >1 weight increases the contributing points (and thus the score)."""
    base = _score(_BIG, Config())
    amp = _score(_BIG, Config(weights={"size": 3.0}))
    base_pts = sum(s.points for s in base[0].signals if s.rule == "size")
    amp_pts = sum(s.points for s in amp[0].signals if s.rule == "size")
    assert amp_pts > base_pts
    assert amp[0].score >= base[0].score


def test_extra_surface_pattern_fires(tmp_path: Path) -> None:
    """A configured surface path adds a signal a default run would miss."""
    text = "\n".join(
        [
            "diff --git a/payments/charge.py b/payments/charge.py",
            "--- a/payments/charge.py",
            "+++ b/payments/charge.py",
            "@@ -1 +1,2 @@",
            " x = 1",
            "+y = 2",
            "",
        ]
    )
    assert _score(text, Config())[0].score == 0
    cfg = load_config(
        explicit=_write(
            tmp_path,
            '[[surface]]\npattern = "(^|/)payments/"\npoints = 12\n'
            'reason = "touches the payments module"\n',
        )
    )
    scored = _score(text, cfg)
    assert scored[0].score > 0
    assert any("payments module" in r for r in scored[0].reasons)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".sommelier.toml"
    p.write_text(body)
    return p


def test_invalid_toml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(explicit=_write(tmp_path, "this is = = not toml"))
    assert "invalid TOML" in str(exc.value)


def test_unknown_rule_weight_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(explicit=_write(tmp_path, "[weights]\nbogus = 2.0\n"))
    assert "unknown rule" in str(exc.value)


def test_negative_weight_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(explicit=_write(tmp_path, "[weights]\nsize = -1\n"))


def test_non_numeric_weight_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(explicit=_write(tmp_path, '[weights]\nsize = "lots"\n'))


def test_surface_requires_fields(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(explicit=_write(tmp_path, '[[surface]]\npattern = "x"\nreason = "y"\n'))
    assert "points" in str(exc.value)


def test_surface_bad_regex_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(
            explicit=_write(
                tmp_path, '[[surface]]\npattern = "(unclosed"\npoints = 5\nreason = "z"\n'
            )
        )
    assert "invalid regex" in str(exc.value)


def test_empty_config_is_default(tmp_path: Path) -> None:
    cfg = load_config(explicit=_write(tmp_path, "\n"))
    assert cfg.is_default
