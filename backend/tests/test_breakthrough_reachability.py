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

from aidigest.generate.importance import classify_day, classify_day_tier
from aidigest.models import Family, ImportanceTier, Story
from aidigest.process.rank import apply_announcement_floor, score_stories


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


# --------------------------------------------------------------------------- #
# Substance gate: virality ALONE must NOT be a breakthrough (the resume/MRI bug)
# --------------------------------------------------------------------------- #


def _viral_single_source(title: str, *, family: Family = Family.COMMUNITY) -> Story:
    """A maximally-viral but UNCORROBORATED post: one source, no citation."""
    return Story(
        id="viral", title=title, family=family,
        item_ids=["v1"], representative_item_id="v1",
        embedding=None, mention_count=1, engagement=1.0, citation=0.0,
        created_at=datetime.now(UTC),
    )


def test_viral_meme_is_not_a_breakthrough(profile: dict) -> None:
    # A single-source HN meme with max engagement must stay BELOW the breakthrough
    # override and out of the top tier — upvotes alone are not a breakthrough.
    story = _viral_single_source("My resume scored 90/100. Oh wait 74. No - 88")
    scored = score_stories([story], interest_vector=None, profile=profile)
    override = float(profile["tiers"]["breakthrough_importance_override"])
    assert scored[0].importance < override, (
        f"uncorroborated viral importance {scored[0].importance} >= override {override}"
    )
    tagged, overall, _quiet = classify_day(scored, profile=profile)
    assert tagged[0].tier != ImportanceTier.BREAKTHROUGH
    assert overall != ImportanceTier.BREAKTHROUGH  # a viral meme can't make a breakthrough day


def test_single_source_release_still_reaches_breakthrough(profile: dict) -> None:
    # The gate must NOT bury a real single-source LAUNCH: release framing is a
    # substance signal, so engagement is not discounted and importance stays high.
    story = _viral_single_source("OpenAI unveils GPT-5.6 Sol", family=Family.COMMUNITY)
    scored = score_stories([story], interest_vector=None, profile=profile)
    override = float(profile["tiers"]["breakthrough_importance_override"])
    assert scored[0].importance >= override, (
        f"single-source RELEASE importance {scored[0].importance} < override {override}"
    )


def test_corroborated_viral_still_reaches_breakthrough(profile: dict) -> None:
    # Cross-source corroboration is a substance signal even without release framing.
    story = _viral_single_source("GLM 5.2 beats Claude in our benchmarks").model_copy(
        update={"mention_count": 4, "item_ids": ["a", "b", "c", "d"]}
    )
    scored = score_stories([story], interest_vector=None, profile=profile)
    override = float(profile["tiers"]["breakthrough_importance_override"])
    assert scored[0].importance >= override


# --------------------------------------------------------------------------- #
# Announcement floor: real news without upvotes must not read as a "quiet day"
# --------------------------------------------------------------------------- #


def _announcement(title: str, family: Family) -> Story:
    """A real announcement with NO engagement (a press release has no upvotes)."""
    return Story(
        id=title[:10], title=title, family=family,
        item_ids=["i"], representative_item_id="i",
        importance=0.05, mention_count=1, engagement=0.0, citation=0.0,
        created_at=datetime.now(UTC),
    )


def test_announcement_floor_lifts_industry_to_notable(profile: dict) -> None:
    # An engagement-less industry deal must lift the day OUT of "quiet" — but never to
    # breakthrough.
    story = _announcement("Anthropic and California forge a Claude deal", Family.INDUSTRY)
    assert classify_day_tier([story], profile=profile) == ImportanceTier.QUIET_DAY  # before
    floored = apply_announcement_floor([story])
    override = float(profile["tiers"]["breakthrough_importance_override"])
    notable = float(profile["tiers"]["notable_day_min_importance"])
    assert notable <= floored[0].importance < override
    assert classify_day_tier(floored, profile=profile) == ImportanceTier.NOTABLE  # after


def test_announcement_floor_release_title_in_community(profile: dict) -> None:
    # A release-titled story floors even if it surfaced under a non-industry family.
    story = _announcement("Mistral releases an open 70B model", Family.COMMUNITY)
    floored = apply_announcement_floor([story])
    assert floored[0].importance >= float(profile["tiers"]["notable_day_min_importance"])


def test_announcement_floor_leaves_non_announcements(profile: dict) -> None:
    # A low-importance community chatter post is NOT floored — only real announcements.
    story = _announcement("anyone else notice gpt is slower today?", Family.COMMUNITY)
    floored = apply_announcement_floor([story])
    assert floored[0].importance == story.importance  # untouched
