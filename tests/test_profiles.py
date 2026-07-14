"""Tests for reviewer profiles (issue #30)."""

from __future__ import annotations

from pathlib import Path

import pytest

from diff_sommelier.config import ConfigError, load_config
from diff_sommelier.parser import parse_diff
from diff_sommelier.profiles import (
    BUILTIN_PROFILES,
    ProfileError,
    apply_profiles,
    compose,
    resolve_profiles,
)
from diff_sommelier.scorer import score_diff

FIXTURES = Path(__file__).parent / "fixtures"


def _scored(name: str):
    text = (FIXTURES / name).read_text()
    return score_diff(parse_diff(text.splitlines(keepends=True)))


def _top(scored):
    return scored[0].hunk.file_path


# --- composition & resolution -------------------------------------------------


def test_compose_multiplies_shared_categories():
    composed = compose([{"auth": 1.5}, {"auth": 2.0, "sql": 1.4}])
    assert composed["auth"] == pytest.approx(3.0)
    assert composed["sql"] == pytest.approx(1.4)


def test_resolve_builtin_names():
    m = resolve_profiles(["backend"])
    assert m == BUILTIN_PROFILES["backend"]


def test_resolve_composes_multiple():
    m = resolve_profiles(["backend", "security"])
    # auth appears in both (1.3 * 1.5) and deps in both (1.15 * 1.3).
    assert m["auth"] == pytest.approx(1.3 * 1.5)
    assert m["deps"] == pytest.approx(1.15 * 1.3)


def test_resolve_unknown_profile_errors():
    with pytest.raises(ProfileError) as exc:
        resolve_profiles(["nope"])
    assert "unknown profile 'nope'" in str(exc.value)
    assert "backend" in str(exc.value)  # lists available


def test_custom_profile_shadows_builtin():
    m = resolve_profiles(["backend"], {"backend": {"sql": 9.0}})
    assert m == {"sql": 9.0}


# --- acceptance: different top hunk under backend vs frontend ------------------


def test_backend_vs_frontend_flip_top_hunk():
    scored = _scored("profile_mix.patch")
    backend = apply_profiles(scored, resolve_profiles(["backend"]), "backend")
    frontend = apply_profiles(scored, resolve_profiles(["frontend"]), "frontend")
    assert _top(backend) != _top(frontend)
    assert _top(backend).endswith("poetry.lock")
    assert _top(frontend).endswith("main.css")


# --- transparency: reweight annotated in reasons ------------------------------


def test_reweight_annotates_reason():
    scored = _scored("profile_mix.patch")
    security = apply_profiles(scored, resolve_profiles(["security"]), "security")
    lock = next(s for s in security if s.hunk.file_path.endswith("poetry.lock"))
    assert any("profile: security" in r for r in lock.reasons)


def test_frontend_marker_boosts_css_score():
    scored = _scored("profile_mix.patch")
    css_before = next(s for s in scored if s.hunk.file_path.endswith("main.css"))
    assert css_before.score == 0  # no surface signal by default
    frontend = apply_profiles(scored, resolve_profiles(["frontend"]), "frontend")
    css_after = next(s for s in frontend if s.hunk.file_path.endswith("main.css"))
    assert css_after.score > 0
    assert any("boosted" in r and "frontend" in r for r in css_after.reasons)


def test_noop_multipliers_preserve_order_and_scores():
    scored = _scored("profile_mix.patch")
    out = apply_profiles(scored, {"auth": 1.0}, "x")
    assert [s.score for s in out] == [s.score for s in scored]
    assert [s.hunk.id for s in out] == [s.hunk.id for s in scored]


# --- config: [profiles.<name>] parsing ---------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".sommelier.toml"
    p.write_text(body)
    return p


def test_config_parses_custom_profile(tmp_path):
    p = _write(tmp_path, "[profiles.myteam]\nfrontend = 2.0\nmigration = 0.5\n")
    cfg = load_config(explicit=p)
    assert cfg.profiles == {"myteam": {"frontend": 2.0, "migration": 0.5}}
    assert not cfg.is_default


def test_config_rejects_unknown_category(tmp_path):
    p = _write(tmp_path, "[profiles.bad]\nnonsense = 2.0\n")
    with pytest.raises(ConfigError) as exc:
        load_config(explicit=p)
    assert "unknown category 'nonsense'" in str(exc.value)


def test_config_rejects_non_numeric_multiplier(tmp_path):
    p = _write(tmp_path, '[profiles.bad]\nauth = "high"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(explicit=p)
    assert "must be a number" in str(exc.value)


def test_config_rejects_negative_multiplier(tmp_path):
    p = _write(tmp_path, "[profiles.bad]\nauth = -1.0\n")
    with pytest.raises(ConfigError):
        load_config(explicit=p)


def test_config_custom_profile_used_by_resolve(tmp_path):
    p = _write(tmp_path, "[profiles.myteam]\nfrontend = 2.0\n")
    cfg = load_config(explicit=p)
    m = resolve_profiles(["myteam"], cfg.profiles)
    assert m == {"frontend": 2.0}
