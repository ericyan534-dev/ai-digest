"""Tests for generate.weekly — best-of-N + judge + polish, shortlist, radar."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from aidigest.generate.weekly import (
    _LONG_FORM_MAX_OUTPUT_TOKENS,
    _fallback_body,
    generate_weekly,
)
from aidigest.llm.base import GenerationResult, JsonSchema, Message
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


# --------------------------------------------------------------------------- #
# Regression: 2026-07-26 blank weekly digest.
#
# Root cause: gemini-3.5-flash is a reasoning model that spends "thoughts"
# tokens from the SAME maxOutputTokens budget as the visible answer. The
# weekly editorial (full narrative body + two link lists as one JSON object)
# is the longest single generation in the system. At the hardcoded 8192-token
# budget the response came back truncated (finishReason=MAX_TOKENS), so the
# JSON failed to parse for both the polish output and the winning candidate,
# and title/lede/body/shortlist/radar all ended up empty — yet the digest was
# still emailed.
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """Fake LLMClient that records every ``generate_detailed`` call's kwargs and
    always answers with a fixed (possibly unparseable) text. Mirrors the shape
    of ``MockLLMClient`` so it structurally satisfies ``LLMClient``."""

    def __init__(self, text: str, *, model: str = "fake-model") -> None:
        self.model = model
        self.embed_model = "fake-embed"
        self.embed_dim = 1536
        self._text = text
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> str:
        result = await self.generate_detailed(
            prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )
        return result.text

    async def generate_detailed(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> GenerationResult:
        self.calls.append(
            {"max_output_tokens": max_output_tokens, "temperature": temperature}
        )
        return GenerationResult(text=self._text, truncated=False, model=self.model)

    async def embed(
        self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> list[list[float]]:
        return [[0.0] * self.embed_dim for _ in texts]

    async def judge(self, *, candidates: list[str], rubric: dict, context: str = "") -> dict:
        return {"winner": 0, "scores": [{} for _ in candidates], "rationale": "fake-judge"}


_VALID_CANDIDATE_JSON = json.dumps(
    {
        "title": "Fake title",
        "lede": "Fake lede",
        "body_markdown": "Fake body",
        "shortlist": [],
        "on_my_radar": [],
    }
)


async def test_generate_weekly_requests_long_form_budget_for_candidates_and_polish() -> None:
    """The exact call that was missing before the fix: candidate generation AND
    the polish pass must both request `_LONG_FORM_MAX_OUTPUT_TOKENS`, not the
    (too small) default. This is the test that would have caught the original bug.
    """
    fake = _FakeLLM(_VALID_CANDIDATE_JSON)
    a = _item("bw1", "Story A", Family.INDUSTRY)
    stories = [_story("bw-1", "Story A", 0.9, Family.INDUSTRY, a)]
    digest = await generate_weekly(
        stories, {a.id: a}, profile=PROFILE, week_of="2026-06-15", llm=fake, n_candidates=2
    )
    # 2 candidate calls + 1 polish call, all on the recording client.
    assert len(fake.calls) == 3
    assert all(
        call["max_output_tokens"] == _LONG_FORM_MAX_OUTPUT_TOKENS for call in fake.calls
    )
    # Sanity: the budget-respecting round trip actually parsed and produced the
    # fake content (proves the wiring works end-to-end, not just the kwarg).
    assert digest.title == "Fake title"
    assert digest.body_markdown == "Fake body"


async def test_generate_weekly_falls_back_to_story_list_when_unparseable() -> None:
    """Never-blank guard: when every candidate AND the polish come back
    unparseable on a non-quiet week, the digest body is the deterministic,
    grounded story list instead of an empty string.
    """
    fake = _FakeLLM("{ truncated json...")
    a = _item("f1", "Frontier RL model", Family.INDUSTRY)
    b = _item("f2", "Linear attention", Family.ACADEMIA)
    stories = [
        _story("f-1", "Frontier RL model", 0.92, Family.INDUSTRY, a),
        _story("f-2", "Linear attention", 0.60, Family.ACADEMIA, b),
    ]
    items_by_id = {a.id: a, b.id: b}
    digest = await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-06-15", llm=fake, n_candidates=2
    )
    assert digest.quiet_week is False
    assert digest.body_markdown.strip()
    assert "Frontier RL model" in digest.body_markdown
    assert "Linear attention" in digest.body_markdown


async def test_generate_weekly_quiet_week_stays_honest_when_unparseable() -> None:
    """Same unparseable-everything scenario, but the week IS quiet: the honest
    quiet-week line ships, not the raw story list (and not blank)."""
    fake = _FakeLLM("{ truncated json...")
    item = _item("qw1", "minor bump", Family.COMMUNITY)
    stories = [_story("qw-1", "minor bump", 0.15, Family.COMMUNITY, item)]
    digest = await generate_weekly(
        stories, {item.id: item}, profile=PROFILE, week_of="2026-06-15", llm=fake
    )
    assert digest.quiet_week is True
    assert digest.body_markdown == "Quiet week — nothing major shipped."


async def test_generate_weekly_no_stories_unparseable_does_not_crash() -> None:
    """No stories at all + unparseable LLM output must still produce a non-empty,
    honest body without raising."""
    fake = _FakeLLM("{ truncated json...")
    digest = await generate_weekly(
        [], {}, profile=PROFILE, week_of="2026-06-15", llm=fake
    )
    assert digest.quiet_week is True
    assert digest.body_markdown.strip()


def test_fallback_body_only_links_urls_present_on_member_items() -> None:
    """Grounding requirement: `_fallback_body` links a story's title ONLY when a
    member item actually carries a URL. A story whose items have no URL renders
    as plain text — we must never fabricate a link."""
    linked = _item("l1", "Has a link", Family.INDUSTRY)
    unlinked = Item.create(
        source="hn",
        family=Family.COMMUNITY,
        title="No source link",
        url=None,
        raw_text="body text",
        published_at=_NOW,
    )
    stories = [
        _story("l-1", "Has a link", 0.9, Family.INDUSTRY, linked),
        Story(
            id="l-2",
            title="No source link",
            family=Family.COMMUNITY,
            item_ids=[unlinked.id],
            representative_item_id=unlinked.id,
            final_rank=0.5,
            importance=0.5,
            mention_count=1,
            created_at=_NOW,
        ),
    ]
    items_by_id = {linked.id: linked, unlinked.id: unlinked}
    body = _fallback_body(stories, items_by_id)
    assert f"[Has a link]({linked.url})" in body
    assert "No source link" in body
    assert "[No source link]" not in body  # no markdown link fabricated


async def test_generate_weekly_logs_error_when_nothing_parses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An operator-visible ERROR is emitted when neither the polish nor the
    winning candidate parses, so the failure is findable in the Actions log."""
    fake = _FakeLLM("{ truncated json...")
    a = _item("e1", "Story E", Family.INDUSTRY)
    stories = [_story("e-1", "Story E", 0.9, Family.INDUSTRY, a)]
    with caplog.at_level(logging.ERROR, logger="aidigest.generate"):
        await generate_weekly(
            stories, {a.id: a}, profile=PROFILE, week_of="2026-06-15", llm=fake
        )
    assert any(
        record.levelno == logging.ERROR and "no parseable JSON" in record.message
        for record in caplog.records
    )
