"""Tests for the risk-tier vocabulary (savor / sip / gulp)."""

from __future__ import annotations

from diff_sommelier.render.tiers import GULP_AT, SIP_AT, Tier, tier_for


def test_thresholds_partition_the_scale() -> None:
    assert tier_for(0) is Tier.SAVOR
    assert tier_for(SIP_AT - 1) is Tier.SAVOR
    assert tier_for(SIP_AT) is Tier.SIP
    assert tier_for(GULP_AT - 1) is Tier.SIP
    assert tier_for(GULP_AT) is Tier.GULP
    assert tier_for(100) is Tier.GULP


def test_tier_is_monotonic_in_score() -> None:
    seen_order = [tier_for(s) for s in range(0, 101)]
    # Once you reach a higher tier you never drop back as score increases.
    rank = {Tier.SAVOR: 0, Tier.SIP: 1, Tier.GULP: 2}
    ranks = [rank[t] for t in seen_order]
    assert ranks == sorted(ranks)


def test_tier_display_metadata_present_and_padded() -> None:
    for t in Tier:
        assert t.label  # non-empty label
        assert len(t.label) == 4  # fixed-width gutter
        assert t.style  # a rich style string
        assert t.key in {"gulp", "sip", "savor"}
