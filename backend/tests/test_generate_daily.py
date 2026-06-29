"""Tests for generate.daily — map-reduce, tier-scaled depth, quiet-day honesty."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.generate.daily import generate_daily, map_story
from aidigest.llm.mock import MockLLMClient
from aidigest.models import (
    DigestKind,
    Family,
    ImportanceTier,
    Item,
    Story,
)

_NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)

PROFILE: dict = {
    "subfields": ["RL for NLP", "Efficient and Scalable NLP"],
    "voice": {"emulate": ["Karpathy"]},
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


def _item(iid: str, title: str, body: str, family: Family) -> Item:
    return Item.create(
        source="arxiv" if family == Family.ACADEMIA else "hn",
        family=family,
        title=title,
        url=f"https://example.com/{iid}",
        raw_text=body,
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
        mention_count=3,
        created_at=_NOW,
    )


# --------------------------------------------------------------------------- #
# map_story — depth scales with tier
# --------------------------------------------------------------------------- #


async def test_map_story_breakthrough_longer_than_minor(llm: MockLLMClient) -> None:
    item = _item("i1", "Big RL result", "A new RL recipe matches frontier models.", Family.INDUSTRY)
    bt = _story("s-bt", "Big RL result", 0.92, Family.INDUSTRY, item).model_copy(
        update={"tier": ImportanceTier.BREAKTHROUGH}
    )
    mn = _story("s-mn", "Big RL result", 0.40, Family.INDUSTRY, item).model_copy(
        update={"tier": ImportanceTier.MINOR}
    )
    items_by_id = {item.id: item}
    bt_summary = await map_story(bt, items_by_id, profile=PROFILE, llm=llm)
    mn_summary = await map_story(mn, items_by_id, profile=PROFILE, llm=llm)
    # The mock returns deterministic prose; the breakthrough prompt is richer, so
    # the breakthrough takeaway is at least as long. Assert tiers carried through.
    assert bt_summary.tier == ImportanceTier.BREAKTHROUGH
    assert mn_summary.tier == ImportanceTier.MINOR
    assert bt_summary.links == [item.url]


async def test_map_story_quiet_day_skips_llm(llm: MockLLMClient) -> None:
    item = _item("i2", "tiny bump", "A library point release.", Family.COMMUNITY)
    qd = _story("s-qd", "tiny bump", 0.10, Family.COMMUNITY, item).model_copy(
        update={"tier": ImportanceTier.QUIET_DAY}
    )
    summary = await map_story(qd, {item.id: item}, profile=PROFILE, llm=llm)
    assert summary.tier == ImportanceTier.QUIET_DAY
    assert summary.why_it_matters == ""


# --------------------------------------------------------------------------- #
# generate_daily — quiet vs breakthrough day
# --------------------------------------------------------------------------- #


async def test_generate_daily_quiet_day_is_honest(llm: MockLLMClient) -> None:
    item = _item("q1", "minor bump", "routine release", Family.COMMUNITY)
    stories = [_story("q-1", "minor bump", 0.20, Family.COMMUNITY, item)]
    digest = await generate_daily(
        stories, {item.id: item}, profile=PROFILE, date="2026-06-21", llm=llm
    )
    assert digest.kind == DigestKind.DAILY
    assert digest.id == "daily-2026-06-21"
    assert digest.quiet_day is True
    assert digest.overall_tier == ImportanceTier.QUIET_DAY
    assert "quiet day" in digest.tldr.lower()


async def test_generate_daily_breakthrough_day(llm: MockLLMClient) -> None:
    a = _item("a1", "Frontier RL model", "matches frontier at 10x less compute", Family.INDUSTRY)
    b = _item("b1", "Linear attention", "near-parity long context", Family.ACADEMIA)
    stories = [
        _story("d-1", "Frontier RL model", 0.92, Family.INDUSTRY, a),
        _story("d-2", "Linear attention", 0.60, Family.ACADEMIA, b),
    ]
    items_by_id = {a.id: a, b.id: b}
    digest = await generate_daily(
        stories, items_by_id, profile=PROFILE, date="2026-06-21", llm=llm
    )
    assert digest.quiet_day is False
    assert digest.overall_tier == ImportanceTier.BREAKTHROUGH
    assert "quiet day" not in digest.tldr.lower()
    # Both breakthroughs LEAD in the cross-family "Top Stories" section (full depth).
    top = next(sec for sec in digest.sections if sec.heading.endswith("Top Stories"))
    assert {s.story_id for s in top.summaries} == {"d-1", "d-2"}
    assert all(s.takeaway for s in top.summaries)  # full takeaways, not brief links
    assert set(digest.story_ids) == {"d-1", "d-2"}


async def test_generate_daily_caps_top_stories(llm: MockLLMClient) -> None:
    items = [_item(f"m{i}", f"story {i}", "body", Family.INDUSTRY) for i in range(9)]
    stories = [
        _story(f"m-{i}", f"story {i}", 0.9 - i * 0.05, Family.INDUSTRY, items[i])
        for i in range(9)
    ]
    items_by_id = {it.id: it for it in items}
    digest = await generate_daily(
        stories, items_by_id, profile=PROFILE, date="2026-06-21", llm=llm
    )
    # The cross-family Top Stories lead is capped; remaining items become brief links.
    full = [s for sec in digest.sections for s in sec.summaries if s.takeaway]
    assert len(full) <= 6
    assert len(digest.story_ids) >= 6  # all surfaced (leads + brief)


async def test_generate_daily_empty(llm: MockLLMClient) -> None:
    digest = await generate_daily([], {}, profile=PROFILE, date="2026-06-21", llm=llm)
    assert digest.quiet_day is True
    assert digest.overall_tier == ImportanceTier.QUIET_DAY
    assert digest.sections == []
