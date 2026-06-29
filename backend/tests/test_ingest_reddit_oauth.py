"""Reddit adapter — app-only OAuth path (token + oauth.reddit.com listings)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402
from pytest_httpx import HTTPXMock  # noqa: E402

import aidigest.ingest.reddit as reddit  # noqa: E402
from aidigest.config import Settings  # noqa: E402

SINCE = datetime(2020, 1, 1, tzinfo=UTC)
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# A recent post (created_utc ~ 2026) above the score floor.
REDDIT_JSON = {
    "data": {
        "children": [
            {
                "data": {
                    "id": "abc",
                    "title": "A strong new open LLM dropped",
                    "score": 420,
                    "num_comments": 88,
                    "created_utc": 1781000000.0,
                    "permalink": "/r/LocalLLaMA/comments/abc/",
                    "author": "someone",
                    "subreddit": "LocalLLaMA",
                }
            }
        ]
    }
}

REDDIT_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>New open LLM released</title>
    <link href="https://www.reddit.com/r/LocalLLaMA/comments/abc/new/"/>
    <updated>2026-06-20T00:00:00+00:00</updated>
    <author><name>/u/someone</name></author>
    <content type="html">A new open model dropped.</content>
  </entry>
</feed>"""

reusable = pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False,
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)


def _oauth_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        AIDIGEST_LLM_MOCK=True,
        REDDIT_CLIENT_ID="id",
        REDDIT_CLIENT_SECRET="secret",
    )


@pytest.mark.asyncio
async def test_get_oauth_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
    assert await reddit._get_oauth_token(_oauth_settings()) == "tok"


@reusable
@pytest.mark.asyncio
async def test_fetch_uses_oauth_endpoint(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
    httpx_mock.add_response(json=REDDIT_JSON)
    monkeypatch.setattr(reddit, "get_settings", _oauth_settings)

    items = await reddit.ADAPTER.fetch(SINCE)

    assert items and items[0].source == "reddit"
    assert items[0].title == "A strong new open LLM dropped"
    urls = [str(r.url) for r in httpx_mock.get_requests()]
    assert any("oauth.reddit.com" in u for u in urls)


@reusable
@pytest.mark.asyncio
async def test_fetch_falls_back_to_rss(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No OAuth creds -> free public hot/.rss path (the .json API is hard-403'd).
    httpx_mock.add_response(text=REDDIT_RSS)
    monkeypatch.setattr(reddit, "get_settings", lambda: Settings(AIDIGEST_LLM_MOCK=True))  # type: ignore[call-arg]

    items = await reddit.ADAPTER.fetch(SINCE)

    assert items
    assert items[0].source == "reddit"
    assert items[0].raw.get("via") == "rss"
    urls = [str(r.url) for r in httpx_mock.get_requests()]
    assert any(".rss" in u for u in urls)
    assert not any("oauth.reddit.com" in u for u in urls)
