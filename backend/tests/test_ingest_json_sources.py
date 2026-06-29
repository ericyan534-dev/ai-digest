"""Tests for JSON-API adapters: HN, Reddit, OpenReview, Semantic Scholar.

Network is mocked with pytest-httpx. Verifies parsing, metrics mapping,
relevance/score gating, the `since` filter, and graceful failure (skip-with-log,
never crash).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pytest_httpx import HTTPXMock

import aidigest.ingest.reddit as reddit_mod
from aidigest.config import Settings
from aidigest.ingest.hn import ADAPTER as HN
from aidigest.ingest.openreview import ADAPTER as OPENREVIEW
from aidigest.ingest.reddit import ADAPTER as REDDIT
from aidigest.ingest.semantic_scholar import ADAPTER as S2
from aidigest.ingest.semantic_scholar import (
    citation_velocity,
    enrich_item,
    enrich_items,
)
from aidigest.models import Family, Item

SINCE = datetime(2020, 1, 1, tzinfo=UTC)

# Failure paths retry (>=5x) and may exhaust fallbacks, so a single registered
# exception must satisfy many requests and not every registration is consumed.
retrying = pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False,
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)


# --------------------------------------------------------------------------- #
# Hacker News
# --------------------------------------------------------------------------- #


async def test_hn_parses_ai_story_and_show_hn(httpx_mock: HTTPXMock) -> None:
    front = {
        "hits": [
            {
                "objectID": "1",
                "title": "New LLM beats GPT-4 on reasoning",
                "url": "https://example.org/llm",
                "author": "alice",
                "points": 240,
                "num_comments": 88,
                "created_at_i": 1_700_000_000,
                "created_at": "2023-11-14T22:13:20Z",
                "_tags": ["story"],
            },
            {
                "objectID": "2",
                "title": "A new espresso machine",  # not AI-relevant -> dropped
                "url": "https://example.org/coffee",
                "points": 500,
                "num_comments": 10,
                "created_at_i": 1_700_000_000,
                "_tags": ["story"],
            },
        ]
    }
    show = {
        "hits": [
            {
                "objectID": "3",
                "title": "Show HN: my tiny inference server",
                "url": "https://example.org/show",
                "points": 12,
                "num_comments": 3,
                "created_at_i": 1_700_000_500,
                "_tags": ["show_hn", "story"],
            }
        ]
    }
    httpx_mock.add_response(json=front)
    httpx_mock.add_response(json=show)

    items = await HN.fetch(SINCE)
    titles = {i.title for i in items}
    assert "New LLM beats GPT-4 on reasoning" in titles
    assert "Show HN: my tiny inference server" in titles  # show_hn bypasses AI gate
    assert "A new espresso machine" not in titles
    llm = next(i for i in items if i.source == "hn" and "LLM" in i.title)
    assert llm.metrics == {"upvotes": 240, "comments": 88}
    assert llm.family == Family.COMMUNITY
    assert llm.author == "alice"


@retrying
async def test_hn_network_failure_returns_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await HN.fetch(SINCE) == []


# --------------------------------------------------------------------------- #
# Reddit
# --------------------------------------------------------------------------- #


def _reddit_listing(posts: list[dict]) -> dict:
    return {"data": {"children": [{"data": p} for p in posts]}}


def _reddit_oauth_settings() -> Settings:
    # The .json mapping/filtering logic runs on the OAuth path (oauth.reddit.com);
    # the no-OAuth path uses RSS (tested in test_ingest_reddit_oauth.py).
    return Settings(  # type: ignore[call-arg]
        AIDIGEST_LLM_MOCK=True, REDDIT_CLIENT_ID="id", REDDIT_CLIENT_SECRET="sec"
    )


_REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


async def test_reddit_parses_posts_and_filters(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reddit_mod, "get_settings", _reddit_oauth_settings)
    httpx_mock.add_response(url=_REDDIT_TOKEN_URL, json={"access_token": "tok"})
    good = {
        "id": "p1",
        "title": "Running Llama 3 locally on a Mac",
        "permalink": "/r/LocalLLaMA/comments/p1/llama/",
        "url": "https://example.org/llama",
        "author": "bob",
        "score": 320,
        "num_comments": 45,
        "created_utc": 1_700_000_000,
        "selftext": "<p>guide</p>",
        "subreddit": "LocalLLaMA",
    }
    low = {**good, "id": "p2", "title": "low score", "score": 1}
    sticky = {**good, "id": "p3", "title": "pinned", "stickied": True, "score": 999}
    listing = _reddit_listing([good, low, sticky])
    # 2 subreddits x 2 listings = 4 responses
    for _ in range(4):
        httpx_mock.add_response(json=listing)

    items = await REDDIT.fetch(SINCE)
    titles = {i.title for i in items}
    assert "Running Llama 3 locally on a Mac" in titles
    assert "low score" not in titles  # below MIN_SCORE
    assert "pinned" not in titles  # stickied
    it = next(i for i in items if i.title.startswith("Running"))
    assert it.metrics == {"upvotes": 320, "comments": 45}
    assert it.url.endswith("/llama/")  # prefers comments permalink
    assert it.raw["external_url"] == "https://example.org/llama"


@retrying
async def test_reddit_partial_failure_isolated(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reddit_mod, "get_settings", _reddit_oauth_settings)
    httpx_mock.add_response(url=_REDDIT_TOKEN_URL, json={"access_token": "tok"})
    listing = _reddit_listing(
        [
            {
                "id": "x1",
                "title": "good post",
                "score": 50,
                "num_comments": 2,
                "created_utc": 1_700_000_000,
                "permalink": "/r/x/1/",
            }
        ]
    )
    # One listing call fails, rest succeed -> adapter must not crash, returns good ones.
    httpx_mock.add_exception(httpx.ConnectError("reset"))
    for _ in range(3):
        httpx_mock.add_response(json=listing)
    items = await REDDIT.fetch(SINCE)
    assert any(i.title == "good post" for i in items)


# --------------------------------------------------------------------------- #
# OpenReview
# --------------------------------------------------------------------------- #


async def test_openreview_parses_v2_value_wrappers(httpx_mock: HTTPXMock) -> None:
    note = {
        "id": "abc123",
        "cdate": 1_700_000_000_000,  # epoch millis
        "invitation": "ICLR.cc/2026/Conference/-/Submission",
        "content": {
            "title": {"value": "Scaling laws for multi-agent RL"},
            "abstract": {"value": "<p>We study...</p>"},
            "authors": {"value": ["Ada L.", "Bo C."]},
            "venue": {"value": "ICLR 2026 Conference"},
        },
    }
    payload = {"notes": [note]}
    # 3 venues x 2 years = 6 requests; respond to all with same payload.
    for _ in range(6):
        httpx_mock.add_response(json=payload)

    items = await OPENREVIEW.fetch(SINCE)
    assert items, "expected at least one note"
    it = items[0]
    assert it.title == "Scaling laws for multi-agent RL"
    assert it.family == Family.ACADEMIA
    assert it.url == "https://openreview.net/forum?id=abc123"
    assert "We study" in it.raw_text
    assert it.author == "Ada L., Bo C."
    # de-duplicated across the repeated payloads
    assert len({i.id for i in items}) == len(items)


@retrying
async def test_openreview_failure_returns_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await OPENREVIEW.fetch(SINCE) == []


# --------------------------------------------------------------------------- #
# Semantic Scholar
# --------------------------------------------------------------------------- #


def test_citation_velocity_recent_window() -> None:
    paper = {
        "citationCount": 50,
        "year": 2024,
        "citations": [
            {"year": 2026},
            {"year": 2026},
            {"year": 2025},
            {"year": 2010},
        ],
    }
    v = citation_velocity(paper, as_of=datetime(2026, 6, 1))
    assert v == round(3 / 2, 3)  # 3 citations in last 2 years / window 2


def test_citation_velocity_falls_back_to_total_over_age() -> None:
    paper = {"citationCount": 40, "year": 2024, "citations": []}
    v = citation_velocity(paper, as_of=datetime(2026, 1, 1))
    assert v == round(40 / 2, 3)


async def test_s2_enrich_item_merges_metrics(httpx_mock: HTTPXMock) -> None:
    item = Item.create(
        source="arxiv",
        family=Family.ACADEMIA,
        title="A paper",
        url="https://arxiv.org/abs/2606.00001",
        raw={"arxiv_id": "2606.00001"},
    )
    httpx_mock.add_response(
        json={
            "citationCount": 12,
            "influentialCitationCount": 3,
            "year": 2026,
            "publicationDate": "2026-06-01",
            "citations": [{"year": 2026}, {"year": 2026}],
        }
    )
    enriched = await enrich_item(item)
    assert enriched.metrics["citations"] == 12
    assert enriched.metrics["influential_citations"] == 3
    assert "citation_velocity" in enriched.metrics
    # immutability preserved
    assert item.metrics == {}


async def test_s2_enrich_no_id_is_noop() -> None:
    item = Item.create(source="hn", family=Family.COMMUNITY, title="X", url="https://e/1")
    assert await enrich_item(item) is item


async def test_s2_fetch_is_noop() -> None:
    assert await S2.fetch(SINCE) == []


@pytest.mark.parametrize("status", [404])
async def test_s2_enrich_handles_404(httpx_mock: HTTPXMock, status: int) -> None:
    item = Item.create(
        source="arxiv",
        family=Family.ACADEMIA,
        title="Missing",
        raw={"arxiv_id": "9999.99999"},
    )
    httpx_mock.add_response(status_code=status)
    out = await enrich_item(item)
    assert out is item  # unchanged on 404


@retrying
async def test_s2_paper_id_from_url(httpx_mock: HTTPXMock) -> None:
    # No arxiv_id in raw; must derive it from the arxiv.org/abs URL.
    item = Item.create(
        source="arxiv",
        family=Family.ACADEMIA,
        title="From URL",
        url="https://arxiv.org/abs/2606.12345v2",
    )
    httpx_mock.add_response(json={"citationCount": 7, "year": 2026, "citations": []})
    out = await enrich_item(item)
    assert out.metrics["citations"] == 7
    # the request path strips the version suffix
    req = httpx_mock.get_requests()[0]
    assert "arXiv:2606.12345" in str(req.url)


async def test_s2_enrich_items_batch(httpx_mock: HTTPXMock) -> None:
    items = [
        Item.create(
            source="arxiv",
            family=Family.ACADEMIA,
            title="P1",
            raw={"arxiv_id": "2606.00001"},
        ),
        Item.create(source="hn", family=Family.COMMUNITY, title="no-id"),
    ]
    # Only the first item has a resolvable id -> exactly one S2 request.
    httpx_mock.add_response(json={"citationCount": 5, "year": 2026, "citations": []})
    out = await enrich_items(items)
    assert len(out) == 2
    assert out[0].metrics["citations"] == 5
    assert out[1].metrics == {}  # untouched
