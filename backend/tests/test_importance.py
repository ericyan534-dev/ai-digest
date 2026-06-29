"""Tier-logic coverage (acceptance gate item (d)).

Exercises classify_tier/classify_day at every boundary, the importance override,
and the OBJECTIVE quiet-day gate (driven by story importance, not final_rank).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.generate.importance import (
    classify_day,
    classify_tier,
    is_quiet_day,
    tier_for,
)
from aidigest.models import Family, ImportanceTier, Story

_NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)

# Mirrors profile.yaml thresholds.
PROFILE: dict = {
    "tiers": {
        "breakthrough_min_score": 0.72,
        "breakthrough_importance_override": 0.55,
        "notable_min_score": 0.40,
        "minor_min_score": 0.30,
        "quiet_day_top_score": 0.40,
        "quiet_day_min_importance": 0.32,
        "notable_day_min_importance": 0.32,
    }
}


def _story(
    score: float,
    *,
    family: Family = Family.INDUSTRY,
    sid: str = "s",
    importance: float = 0.0,
) -> Story:
    """A story with final_rank=score and an explicit importance (default 0.0 so the
    importance override stays OFF unless a test opts in)."""
    return Story(
        id=f"{sid}-{int(score * 100)}",
        title=f"story {score}",
        family=family,
        item_ids=["a"],
        representative_item_id="a",
        importance=importance,
        final_rank=score,
        created_at=_NOW,
    )


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.95, ImportanceTier.BREAKTHROUGH),
        (0.72, ImportanceTier.BREAKTHROUGH),  # boundary: >= breakthrough
        (0.71, ImportanceTier.NOTABLE),
        (0.40, ImportanceTier.NOTABLE),  # boundary: >= notable
        (0.39, ImportanceTier.MINOR),
        (0.30, ImportanceTier.MINOR),  # boundary: >= minor
        (0.29, ImportanceTier.QUIET_DAY),
        (0.0, ImportanceTier.QUIET_DAY),
    ],
)
def test_classify_tier_boundaries(score: float, expected: ImportanceTier) -> None:
    # Non-quiet day (quiet_day=False) + importance 0 -> bands tested on final_rank.
    tier = classify_tier(
        _story(score), profile=PROFILE, day_top_score=0.95, quiet_day=False
    )
    assert tier == expected


def test_importance_override_forces_breakthrough() -> None:
    # A modest final_rank but very high OBJECTIVE importance => BREAKTHROUGH
    # (the "cannot miss it" guarantee, independent of personal/final_rank).
    story = _story(0.50, importance=0.80)
    tier = classify_tier(story, profile=PROFILE, day_top_score=0.95, quiet_day=False)
    assert tier == ImportanceTier.BREAKTHROUGH


def test_quiet_day_gate_caps_top_story_to_minor() -> None:
    top = _story(0.35)
    tier = classify_tier(top, profile=PROFILE, day_top_score=0.35, quiet_day=True)
    # Best-but-thin -> at most a one-line MINOR, never NOTABLE/BREAKTHROUGH.
    assert tier == ImportanceTier.MINOR


def test_quiet_day_gate_non_top_story_is_quiet() -> None:
    weak = _story(0.20)
    tier = classify_tier(weak, profile=PROFILE, day_top_score=0.35, quiet_day=True)
    assert tier == ImportanceTier.QUIET_DAY


def test_classify_day_quiet() -> None:
    # Low importance everywhere => an honest quiet day.
    stories = [_story(0.35, sid="a"), _story(0.10, sid="b")]
    tagged, overall, quiet = classify_day(stories, profile=PROFILE)
    assert quiet is True
    assert overall == ImportanceTier.QUIET_DAY
    assert all(s.tier in (ImportanceTier.MINOR, ImportanceTier.QUIET_DAY) for s in tagged)


def test_classify_day_breakthrough() -> None:
    stories = [
        _story(0.92, sid="a", importance=0.90),  # high importance => breakthrough
        _story(0.60, sid="b", importance=0.40),  # mid final_rank => notable
        _story(0.35, sid="c", importance=0.30),  # below notable floor => minor
    ]
    tagged, overall, quiet = classify_day(stories, profile=PROFILE)
    assert quiet is False
    assert overall == ImportanceTier.BREAKTHROUGH
    tiers = {s.id: s.tier for s in tagged}
    assert tiers["a-92"] == ImportanceTier.BREAKTHROUGH
    assert tiers["b-60"] == ImportanceTier.NOTABLE
    assert tiers["c-35"] == ImportanceTier.MINOR


def test_classify_day_empty() -> None:
    tagged, overall, quiet = classify_day([], profile=PROFILE)
    assert tagged == []
    assert overall == ImportanceTier.QUIET_DAY
    assert quiet is True


def test_classify_day_returns_new_copies() -> None:
    s = _story(0.92, importance=0.90)
    tagged, _, _ = classify_day([s], profile=PROFILE)
    assert tagged[0] is not s  # immutable copy
    assert s.tier == ImportanceTier.MINOR  # original default untouched


def test_is_quiet_day_helper() -> None:
    assert is_quiet_day([_story(0.35)], profile=PROFILE) is True  # importance 0
    assert is_quiet_day([_story(0.92, importance=0.90)], profile=PROFILE) is False
    assert is_quiet_day([], profile=PROFILE) is True


def test_tier_for_helper_in_isolation() -> None:
    # final_rank 0.92 with the legacy (final_rank) standalone gate => breakthrough.
    assert tier_for(_story(0.92), profile=PROFILE) == ImportanceTier.BREAKTHROUGH
    # A weak story judged in isolation hits the legacy quiet gate.
    assert tier_for(_story(0.20), profile=PROFILE) == ImportanceTier.QUIET_DAY


def test_default_thresholds_when_profile_missing_tiers() -> None:
    # Empty profile -> defaults kick in, no crash.
    tier = classify_tier(_story(0.92), profile={}, day_top_score=0.92)
    assert tier == ImportanceTier.BREAKTHROUGH


# --------------------------------------------------------------------------- #
# Golden-set integration: busy -> BREAKTHROUGH (full depth); quiet -> QUIET_DAY.
# --------------------------------------------------------------------------- #


def test_golden_busy_set_classifies_breakthrough(profile: dict, busy_stories) -> None:
    tagged, overall, quiet = classify_day(busy_stories, profile=profile)
    assert quiet is False
    assert overall == ImportanceTier.BREAKTHROUGH
    top = max(tagged, key=lambda s: s.final_rank)
    assert top.tier == ImportanceTier.BREAKTHROUGH


def test_golden_quiet_set_classifies_quiet_day(profile: dict, quiet_stories) -> None:
    tagged, overall, quiet = classify_day(quiet_stories, profile=profile)
    assert quiet is True
    assert overall == ImportanceTier.QUIET_DAY
    assert all(
        s.tier in (ImportanceTier.MINOR, ImportanceTier.QUIET_DAY) for s in tagged
    )


def test_profile_yaml_thresholds_match_test_profile(profile: dict) -> None:
    # Guards against drift between profile.yaml and the literal PROFILE above.
    tiers = profile["tiers"]
    for key in (
        "breakthrough_min_score",
        "breakthrough_importance_override",
        "notable_min_score",
        "minor_min_score",
        "quiet_day_top_score",
        "quiet_day_min_importance",
        "notable_day_min_importance",
    ):
        assert tiers[key] == PROFILE["tiers"][key], f"{key} drifted"
