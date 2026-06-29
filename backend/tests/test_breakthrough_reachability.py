"""Reachability tests for the "cannot miss a breakthrough" guarantee.

The flexibility principle promises that a genuinely revolutionary story is never
silently downgraded. That promise lives in two places and BOTH are tested here:

1. The ranking layer must be able to actually PRODUCE a breakthrough-grade
   ``final_rank`` for a strong, on-subfield story (guards against threshold /
   weight drift making the top tier mathematically unreachable).
2. A story with very high RAW importance must classify as BREAKTHROUGH even when
   the personal/embedding signal is absent (cold start, mock mode, missing
   embeddings) — via the embedding-independent override.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aidigest.generate.importance import classify_day
from aidigest.models import Family, ImportanceTier, Story
from aidigest.process.rank import score_stories


def _strong_story(*, embedding: list[float] | None) -> Story:
    """A maximally-strong academia story: many cross-source mentions, high real
    engagement (the world is reacting), fresh."""
    return Story(
        id="strong-breakthrough",
        title="A revolutionary RL post-training result",
        family=Family.ACADEMIA,
        item_ids=["x1", "x2", "x3"],
        representative_item_id="x1",
        embedding=embedding,
        mention_count=20,
        engagement=1.0,
        citation=0.8,
        created_at=datetime.now(UTC),
    )


def test_strong_on_subfield_story_reaches_breakthrough(
    profile: dict, interest_vector: list[float]
) -> None:
    story = _strong_story(embedding=interest_vector)  # cosine 1.0 -> personal max
    scored = score_stories([story], interest_vector=interest_vector, profile=profile)
    threshold = float(profile["tiers"]["breakthrough_min_score"])
    assert scored[0].final_rank >= threshold, (
        f"strong story final_rank {scored[0].final_rank} < breakthrough threshold "
        f"{threshold} — the blended ranking can never reach the top tier"
    )
    tagged, overall, quiet = classify_day(scored, profile=profile)
    assert quiet is False
    assert tagged[0].tier == ImportanceTier.BREAKTHROUGH
    assert overall == ImportanceTier.BREAKTHROUGH


def test_breakthrough_survives_missing_embedding(profile: dict) -> None:
    # No embedding => personal score 0, so the blended final_rank alone falls
    # short of breakthrough_min_score. A very high RAW importance must still force
    # BREAKTHROUGH so a real release is not lost just because personalization is cold.
    story = _strong_story(embedding=None)
    scored = score_stories([story], interest_vector=None, profile=profile)
    override = float(profile["tiers"]["breakthrough_importance_override"])
    assert scored[0].importance >= override, (
        f"raw importance {scored[0].importance} < override {override}"
    )
    tagged, _overall, _quiet = classify_day(scored, profile=profile)
    assert tagged[0].tier == ImportanceTier.BREAKTHROUGH
