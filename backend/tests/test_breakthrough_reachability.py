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
from aidigest.models import Family, ImportanceTier, Item, Story
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


def _item_with_body(body: str) -> Item:
    """An Item carrying a specific raw_text body (for floor grounding tests)."""
    return Item.create(
        source="hn",
        family=Family.INDUSTRY,
        title="Some announcement",
        url="https://example.com/release",
        raw_text=body,
        published_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------- #
# New floor semantics:
#   floor ONLY when is_release_title AND (mention_count >= 2 OR body >= 200 chars)
#   INDUSTRY alone NO LONGER triggers the floor
# --------------------------------------------------------------------------- #


def test_announcement_floor_industry_alone_not_floored(profile: dict) -> None:
    """INDUSTRY family alone (no release title, no corroboration) must NOT floor.

    Old behaviour floored all INDUSTRY stories. New spec: family is irrelevant —
    only release framing + corroboration/body trigger the floor.
    """
    story = _announcement("Anthropic and California forge a Claude deal", Family.INDUSTRY)
    floored = apply_announcement_floor([story], {})
    assert floored[0].importance == story.importance  # untouched — INDUSTRY alone is not enough


def test_announcement_floor_release_title_corroborated(profile: dict) -> None:
    """Release-titled story with mention_count >= 2 IS floored to notable band."""
    story = _announcement("Mistral releases an open 70B model", Family.COMMUNITY).model_copy(
        update={"mention_count": 2}
    )
    floored = apply_announcement_floor([story], {})
    override = float(profile["tiers"]["breakthrough_importance_override"])
    notable = float(profile["tiers"]["notable_day_min_importance"])
    assert notable <= floored[0].importance < override
    assert classify_day_tier(floored, profile=profile) == ImportanceTier.NOTABLE


def test_announcement_floor_release_title_single_source_no_body_not_floored(
    profile: dict,
) -> None:
    """Release title + mention_count=1 + no body → headline-only blip, NOT floored.

    This is the key precision fix: uncorroborated headline-only announcements must
    not erase quiet days by artificially lifting importance.
    """
    story = _announcement("Acme AI releases v2.0", Family.INDUSTRY)
    floored = apply_announcement_floor([story], {})  # empty items_by_id → body=0
    assert floored[0].importance == story.importance  # untouched


def test_announcement_floor_release_title_single_source_with_body(profile: dict) -> None:
    """Release title + mention_count=1 + body >= 200 chars IS floored.

    A long body signals a substantive announcement, not a bare headline.
    """
    body = "x" * 400  # 400 chars > 200 threshold
    item = _item_with_body(body)
    story = Story(
        id="release-with-body",
        title="OpenAI releases GPT-5 Sol",
        family=Family.INDUSTRY,
        item_ids=[item.id],
        representative_item_id=item.id,
        importance=0.05, mention_count=1, engagement=0.0, citation=0.0,
        created_at=datetime.now(UTC),
    )
    floored = apply_announcement_floor([story], {item.id: item})
    assert floored[0].importance >= float(profile["tiers"]["notable_day_min_importance"])


def test_announcement_floor_leaves_non_announcements(profile: dict) -> None:
    # A low-importance community chatter post is NOT floored — only real announcements.
    story = _announcement("anyone else notice gpt is slower today?", Family.COMMUNITY)
    floored = apply_announcement_floor([story], {})
    assert floored[0].importance == story.importance  # untouched


# --------------------------------------------------------------------------- #
# End-to-end quiet-day: uncorroborated headline-only INDUSTRY blips → QUIET
# --------------------------------------------------------------------------- #


def test_quiet_day_uncorroborated_headline_only_blips(profile: dict) -> None:
    """A day whose only stories are uncorroborated headline-only INDUSTRY blips must
    classify as QUIET via classify_day (importance stays below 0.32).

    This is the 'fake corroboration' regression: smol.ai cross-linking an announcement
    gives mention_count=2 but the announcement has NO real engagement, citations, or
    body.  The floor must NOT fire on uncorroborated no-body stories, so these days
    remain honestly quiet.
    """
    blips = [
        Story(
            id=f"blip-{i}",
            title=f"Some company announces product {i}",
            family=Family.INDUSTRY,
            item_ids=[f"b{i}"],
            representative_item_id=f"b{i}",
            importance=0.0,  # no engagement, no citation → will be scored low
            mention_count=1,
            engagement=0.0,
            citation=0.0,
            created_at=datetime.now(UTC),
        )
        for i in range(4)
    ]
    # score_stories will compute importance; these have 0 engagement + 0 citation + 1 mention
    scored = score_stories(blips, interest_vector=None, profile=profile)
    # Apply the new floor: release title needed — none of these titles have release words
    floored = apply_announcement_floor(scored, {})
    _tagged, _overall, quiet = classify_day(floored, profile=profile)
    quiet_threshold = float(profile["tiers"]["quiet_day_min_importance"])
    assert max(s.importance for s in floored) < quiet_threshold, (
        "headline-only blips should not push importance above the quiet-day gate"
    )
    assert quiet is True, "a day of headline-only blips should classify as QUIET"
