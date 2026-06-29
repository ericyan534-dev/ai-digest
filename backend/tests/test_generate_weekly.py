"""Tests for generate.weekly — best-of-N + judge + polish, shortlist, radar."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.generate.weekly import generate_weekly
from aidigest.llm.mock import MockLLMClient
from aidigest.models import DigestKind, Family, ImportanceTier, Item, Story

_NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)

PROFILE: dict = {
    "subfields": ["RL for NLP", "Optimization"],
    "voice": {"emulate": ["Karpathy"]},
    "venues": ["NeurIPS", "ACL"],
    "ranking": {"alpha": 0.5, "beta": 0.4, "gamma": 0.1},
    "tiers": {
        "breakthrough_min_score": 0.85,
        "notable_min_score": 0.55,
        "minor_min_score": 0.30,
        "quiet_day_top_score": 0.40,
    },
}


@pytest.fixture
def llm() -> MockLLMClient:
    return MockLLMClient(embed_dim=1536)


def _item(iid: str, title: str, family: Family) -> Item:
    return Item.create(
        source="arxiv",
        family=family,
        title=title,
        url=f"https://example.com/{iid}",
        raw_text=f"body for {title}",
        published_at=_NOW,
    )


def _story(sid: str, title: str, score: float, family: Family, item: Item) -> Story:
    return Story(
        id=sid,
        title=title,
        family=family,
        item_ids=[item.id],
        representative_item_id=item.id,
        final_rank=score,
        importance=score,
        mention_count=4,
        created_at=_NOW,
    )


async def test_generate_weekly_basic(llm: MockLLMClient) -> None:
    a = _item("w1", "Frontier RL model", Family.INDUSTRY)
    b = _item("w2", "Linear attention", Family.ACADEMIA)
    stories = [
        _story("w-1", "Frontier RL model", 0.92, Family.INDUSTRY, a),
        _story("w-2", "Linear attention", 0.60, Family.ACADEMIA, b),
    ]
    items_by_id = {a.id: a, b.id: b}
    digest = await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-06-15", llm=llm, n_candidates=3
    )
    assert digest.kind == DigestKind.WEEKLY
    assert digest.id.startswith("weekly-2026-W")
    assert digest.candidate_count == 3
    assert 0 <= digest.winning_candidate < 3
    assert digest.overall_tier == ImportanceTier.BREAKTHROUGH
    assert digest.quiet_week is False
    assert digest.title
    assert digest.story_ids == ["w-1", "w-2"]


async def test_generate_weekly_quiet_week(llm: MockLLMClient) -> None:
    item = _item("qw", "minor bump", Family.COMMUNITY)
    stories = [_story("qw-1", "minor bump", 0.15, Family.COMMUNITY, item)]
    digest = await generate_weekly(
        stories, {item.id: item}, profile=PROFILE, week_of="2026-06-15", llm=llm
    )
    assert digest.quiet_week is True
    assert digest.overall_tier == ImportanceTier.QUIET_DAY


async def test_generate_weekly_iso_week_fallback(llm: MockLLMClient) -> None:
    item = _item("x", "x", Family.INDUSTRY)
    stories = [_story("x-1", "x", 0.9, Family.INDUSTRY, item)]
    digest = await generate_weekly(
        stories, {item.id: item}, profile=PROFILE, week_of="not-a-date", llm=llm
    )
    assert digest.id == "weekly-not-a-date"


async def test_generate_weekly_n1(llm: MockLLMClient) -> None:
    item = _item("n", "n", Family.INDUSTRY)
    stories = [_story("n-1", "n", 0.9, Family.INDUSTRY, item)]
    digest = await generate_weekly(
        stories, {item.id: item}, profile=PROFILE, week_of="2026-06-15", llm=llm, n_candidates=1
    )
    assert digest.candidate_count == 1
    assert digest.winning_candidate == 0
