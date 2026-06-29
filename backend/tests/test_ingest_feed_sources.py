"""Tests for feed-based adapters: arXiv (Atom), RSS, smol.ai, HF papers.

Network is mocked with pytest-httpx by returning canned Atom/RSS XML. Verifies
entry parsing, the `since` date filter, category filtering (arXiv), metric
extraction (HF upvotes), and graceful failure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pytest_httpx import HTTPXMock

from aidigest.ingest.arxiv import ADAPTER as ARXIV
from aidigest.ingest.hf_papers import ADAPTER as HF
from aidigest.ingest.rss import RSSAdapter
from aidigest.ingest.smolai import ADAPTER as SMOLAI
from aidigest.models import Family

SINCE = datetime(2020, 1, 1, tzinfo=UTC)

# Failure paths retry (>=5x) and may exhaust fallback feeds, so one registered
# exception must satisfy many requests and not every registration is consumed.
retrying = pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False,
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2606.00001v1</id>
    <updated>2026-06-20T00:00:00Z</updated>
    <published>2026-06-20T00:00:00Z</published>
    <title>Efficient attention for long context</title>
    <summary>A new linear attention variant for scalable NLP.</summary>
    <author><name>Ada Lovelace</name></author>
    <link href="http://arxiv.org/abs/2606.00001v1" rel="alternate" type="text/html"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2606.00002v1</id>
    <published>2026-06-19T00:00:00Z</published>
    <title>Old robotics paper</title>
    <summary>Not in our categories.</summary>
    <link href="http://arxiv.org/abs/2606.00002v1" rel="alternate"/>
    <category term="cs.RO" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Anthropic</title>
  <item>
    <title>Claude gets better at code</title>
    <link>https://www.anthropic.com/news/claude-code</link>
    <description>&lt;p&gt;A new model with stronger coding.&lt;/p&gt;</description>
    <pubDate>Fri, 20 Jun 2026 10:00:00 GMT</pubDate>
    <author>anthropic</author>
  </item>
  <item>
    <title>Old post</title>
    <link>https://www.anthropic.com/news/old</link>
    <description>stale</description>
    <pubDate>Mon, 01 Jan 1999 10:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

SMOLAI_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>AINews</title>
  <item>
    <title>[AINews] DeepSeek V4, new RL recipe</title>
    <link>https://news.smol.ai/issues/26-06-20</link>
    <description>&lt;p&gt;Today in AI...&lt;/p&gt;</description>
    <pubDate>Sat, 20 Jun 2026 06:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

HF_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>HF Daily Papers</title>
  <item>
    <title>A Survey of Multi-Agent Systems</title>
    <link>https://huggingface.co/papers/2606.01234</link>
    <description>Great paper. 142 upvotes. arXiv 2606.01234</description>
    <pubDate>Fri, 20 Jun 2026 00:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

HF_JSON = """[
  {"publishedAt": "2026-06-20T00:00:00.000Z",
   "paper": {"id": "2606.01234", "title": "A Survey of Multi-Agent Systems",
             "summary": "A broad survey of multi-agent systems for LLMs.",
             "upvotes": 142, "authors": [{"name": "Ada Lovelace"}]}}
]"""

HF_API_URL = "https://huggingface.co/api/daily_papers"


# --------------------------------------------------------------------------- #
# arXiv
# --------------------------------------------------------------------------- #


async def test_arxiv_parses_and_filters_categories(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(text=ARXIV_ATOM)
    items = await ARXIV.fetch(SINCE)
    titles = {i.title for i in items}
    assert "Efficient attention for long context" in titles
    assert "Old robotics paper" not in titles  # cs.RO filtered out
    it = next(i for i in items if i.title.startswith("Efficient"))
    assert it.family == Family.ACADEMIA
    assert it.raw["arxiv_id"] == "2606.00001v1"
    assert "cs.CL" in it.raw["categories"]
    assert it.author == "Ada Lovelace"


async def test_arxiv_respects_since(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(text=ARXIV_ATOM)
    items = await ARXIV.fetch(datetime(2026, 6, 21, tzinfo=UTC))  # after both entries
    assert items == []


@retrying
async def test_arxiv_network_failure(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await ARXIV.fetch(SINCE) == []


# --------------------------------------------------------------------------- #
# Generic RSS
# --------------------------------------------------------------------------- #


async def test_rss_adapter_parses_recent_only(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(text=RSS_XML)
    adapter = RSSAdapter(
        name="anthropic", url="https://x/feed", family=Family.INDUSTRY
    )
    assert adapter.name == "rss:anthropic"
    items = await adapter.fetch(SINCE)
    titles = {i.title for i in items}
    assert "Claude gets better at code" in titles
    assert "Old post" not in titles  # 1999 < SINCE
    it = next(iter(items))
    assert it.family == Family.INDUSTRY
    assert "stronger coding" in it.raw_text
    assert it.source == "rss:anthropic"


@retrying
async def test_rss_adapter_handles_failure(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"))
    adapter = RSSAdapter(name="x", url="https://x/feed", family=Family.META)
    assert await adapter.fetch(SINCE) == []


def test_rss_name_not_double_prefixed() -> None:
    a = RSSAdapter(name="rss:already", url="u", family=Family.META)
    assert a.name == "rss:already"


# --------------------------------------------------------------------------- #
# smol.ai
# --------------------------------------------------------------------------- #


async def test_smolai_parses_issue(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(text=SMOLAI_RSS)
    items = await SMOLAI.fetch(SINCE)
    assert items
    it = items[0]
    assert it.source == "smol.ai"
    assert it.family == Family.META
    assert "DeepSeek V4" in it.title
    assert it.url.endswith("26-06-20")


async def test_smolai_tries_fallback_feeds(httpx_mock: HTTPXMock) -> None:
    # First feed empty, second feed has content.
    httpx_mock.add_response(text="<rss><channel></channel></rss>")
    httpx_mock.add_response(text=SMOLAI_RSS)
    items = await SMOLAI.fetch(SINCE)
    assert any("DeepSeek" in i.title for i in items)


# --------------------------------------------------------------------------- #
# HF papers
# --------------------------------------------------------------------------- #


async def test_hf_papers_extracts_upvotes_and_arxiv_id(httpx_mock: HTTPXMock) -> None:
    # Primary path: the HF daily-papers JSON API.
    httpx_mock.add_response(url=HF_API_URL, text=HF_JSON)
    items = await HF.fetch(SINCE)
    assert items
    it = items[0]
    assert it.family == Family.ACADEMIA
    assert it.metrics.get("hf_upvotes") == 142
    assert it.raw["arxiv_id"] == "2606.01234"


@retrying
async def test_hf_papers_falls_back_to_rss(httpx_mock: HTTPXMock) -> None:
    # JSON API returns junk -> adapter falls back to the RSS feed.
    httpx_mock.add_response(url=HF_API_URL, text="not json")
    httpx_mock.add_response(text=HF_RSS)
    items = await HF.fetch(SINCE)
    assert items
    assert items[0].raw["arxiv_id"] == "2606.01234"
    assert items[0].metrics.get("hf_upvotes") == 142


@retrying
async def test_hf_papers_all_feeds_fail(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await HF.fetch(SINCE) == []
