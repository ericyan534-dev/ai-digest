"""Model-layer tests: the typed spine must be immutable, hashable, JSON-safe.

These extend the foundation contract tests with the invariants the rest of the
harness relies on (idempotent ids, immutability, enum round-trips, digest
serialization for the API).
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aidigest.models import (
    DailyDigest,
    DigestKind,
    DigestSection,
    Family,
    Feedback,
    FeedbackSignal,
    FeedbackTargetKind,
    ImportanceTier,
    Item,
    Source,
    Story,
    StorySummary,
    WeeklyDigest,
    WeeklyShortlistEntry,
    content_hash,
    slugify,
)


def test_item_create_derives_content_hash_id() -> None:
    a = Item.create(source="hn", family=Family.COMMUNITY, title="X", url="https://e/1")
    b = Item.create(source="hn", family=Family.COMMUNITY, title="X", url="https://e/1")
    assert a.id == b.id and len(a.id) == 64


def test_item_create_hashes_text_when_no_url() -> None:
    a = Item.create(source="hn", family=Family.COMMUNITY, title="Same", raw_text="body")
    b = Item.create(source="hn", family=Family.COMMUNITY, title="Same", raw_text="body")
    c = Item.create(source="hn", family=Family.COMMUNITY, title="Other", raw_text="body")
    assert a.id == b.id
    assert a.id != c.id


def test_item_is_frozen() -> None:
    it = Item.create(source="hn", family=Family.COMMUNITY, title="X")
    with pytest.raises(ValidationError):
        it.title = "Y"  # type: ignore[misc]


def test_item_with_embedding_is_immutable_copy() -> None:
    it = Item.create(source="hn", family=Family.COMMUNITY, title="X")
    it2 = it.with_embedding([0.1] * 1536)
    assert it.embedding is None
    assert it2.embedding is not None and len(it2.embedding) == 1536
    assert it2.id == it.id  # identity preserved


def test_story_slug_property() -> None:
    s = Story(id="s1", title="DeepSeek V4: Release!", family=Family.INDUSTRY)
    assert s.slug == "deepseek-v4-release"


def test_story_defaults_minor_tier() -> None:
    s = Story(id="s1", title="t", family=Family.META)
    assert s.tier == ImportanceTier.MINOR
    assert s.mention_count == 1


def test_content_hash_url_normalized() -> None:
    assert content_hash(url="https://E/1", text="a") == content_hash(url="https://e/1", text="b")


def test_slugify_truncates_and_defaults() -> None:
    assert slugify("a" * 200, max_len=10) == "a" * 10
    assert slugify("!!!") == "untitled"


def test_source_authority_bounds() -> None:
    Source(name="hn", family=Family.COMMUNITY, authority=0.0)
    Source(name="hn", family=Family.COMMUNITY, authority=1.0)
    with pytest.raises(ValidationError):
        Source(name="hn", family=Family.COMMUNITY, authority=1.5)


@pytest.mark.parametrize(
    "tier",
    [
        ImportanceTier.BREAKTHROUGH,
        ImportanceTier.NOTABLE,
        ImportanceTier.MINOR,
        ImportanceTier.QUIET_DAY,
    ],
)
def test_importance_tier_round_trips_json(tier: ImportanceTier) -> None:
    s = StorySummary(
        story_id="s", title="t", family=Family.ACADEMIA, tier=tier,
        takeaway="t", why_it_matters="w",
    )
    again = StorySummary.model_validate(s.model_dump(mode="json"))
    assert again.tier == tier


def test_daily_digest_json_round_trip() -> None:
    d = DailyDigest(
        id="daily-2026-06-21", date="2026-06-21", tldr="t",
        overall_tier=ImportanceTier.NOTABLE,
        sections=[
            DigestSection(
                family=Family.ACADEMIA, heading="Academia",
                summaries=[
                    StorySummary(
                        story_id="s", title="t", family=Family.ACADEMIA,
                        tier=ImportanceTier.NOTABLE, takeaway="x", why_it_matters="y",
                        links=["https://e/1"], tags=["LLMs"], score=0.7,
                    )
                ],
            )
        ],
        story_ids=["s"],
    )
    assert d.kind == DigestKind.DAILY
    payload = d.model_dump(mode="json")
    assert DailyDigest.model_validate(payload) == d
    # embeddings never appear in a digest payload
    assert "embedding" not in payload


def test_weekly_digest_json_round_trip() -> None:
    w = WeeklyDigest(
        id="weekly-2026-W25", week_of="2026-06-15", title="T", lede="L",
        body_markdown="# body", overall_tier=ImportanceTier.NOTABLE,
        shortlist=[WeeklyShortlistEntry(title="a", url="https://e/1", one_liner="o",
                                        family=Family.ACADEMIA)],
        on_my_radar=[WeeklyShortlistEntry(title="b", one_liner="o",
                                          family=Family.ACADEMIA)],
        candidate_count=3, winning_candidate=1,
        eval_scores={"insight": 4.0},
    )
    assert w.kind == DigestKind.WEEKLY
    assert WeeklyDigest.model_validate(w.model_dump(mode="json")) == w


def test_feedback_signal_and_kind_enums() -> None:
    fb = Feedback(
        target_id="x", target_kind=FeedbackTargetKind.STORY,
        signal=FeedbackSignal.UP, value=1.0,
    )
    assert fb.id is None  # assigned by DB
    payload = fb.model_dump(mode="json")
    assert payload["signal"] == "up"
    assert payload["target_kind"] == "story"


def test_story_embedding_is_unit_when_set() -> None:
    s = Story(id="s", title="t", family=Family.META, embedding=[0.6, 0.8])
    norm = math.hypot(*s.embedding) if s.embedding else 0.0
    assert math.isclose(norm, 1.0)
