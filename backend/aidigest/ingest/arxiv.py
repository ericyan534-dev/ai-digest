"""arXiv adapter — arXiv API (Atom), filtered to cs.CL / cs.LG / cs.AI by date.

Queries the public arXiv export API (HTTP GET, Atom XML) for the categories this
engine cares about, sorted by submission date, and keeps entries at-or-after
`since`. Parsed with feedparser (Atom). No key required.

The query is OAI-PMH-friendly in spirit: category-scoped + date-windowed so it
maps cleanly onto a future OAI-PMH `ListRecords` harvest if we switch transports.

API: https://export.arxiv.org/api/query
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import feedparser

from aidigest.ingest._feed import entry_author, entry_text, iter_recent_entries
from aidigest.ingest._util import log
from aidigest.ingest.base import fetch_text, make_async_client
from aidigest.models import Family, Item

CATEGORIES: tuple[str, ...] = ("cs.CL", "cs.LG", "cs.AI")
_API = "https://export.arxiv.org/api/query"
_MAX_RESULTS = 200


def _build_query() -> str:
    cat_clause = "+OR+".join(f"cat:{c}" for c in CATEGORIES)
    params = {
        "search_query": cat_clause,
        "start": 0,
        "max_results": _MAX_RESULTS,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    # urlencode would percent-encode the '+OR+' joins we need literally, so build
    # the search_query separately and append the rest.
    rest = urlencode(
        {k: v for k, v in params.items() if k != "search_query"}, safe=""
    )
    return f"{_API}?search_query={cat_clause}&{rest}"


def _arxiv_categories(entry: Any) -> list[str]:
    tags = entry.get("tags") or []
    return [t.get("term") for t in tags if t.get("term")]


def _entry_to_item(entry: Any, published: datetime) -> Item | None:
    title = (entry.get("title") or "").replace("\n", " ").strip()
    if not title:
        return None
    # Prefer the canonical abstract page link; fall back to entry id.
    url = entry.get("link") or entry.get("id") or None
    abstract = entry_text(entry, max_len=6000)
    cats = _arxiv_categories(entry)
    arxiv_id = (entry.get("id") or "").rsplit("/", 1)[-1]
    return Item.create(
        source="arxiv",
        family=Family.ACADEMIA,
        title=title,
        url=url,
        author=entry_author(entry),
        published_at=published,
        raw_text=abstract,
        metrics={},
        raw={"arxiv_id": arxiv_id, "categories": cats},
    )


class ArxivAdapter:
    """Recent cs.CL/cs.LG/cs.AI submissions from the arXiv Atom API."""

    name = "arxiv"
    family = Family.ACADEMIA

    async def fetch(self, since: datetime) -> list[Item]:
        url = _build_query()
        try:
            async with make_async_client() as client:
                text = await fetch_text(url, client=client)
        except Exception as exc:
            log.warning("arxiv adapter failed to fetch: %s", exc)
            return []

        parsed = feedparser.parse(text)
        if parsed.bozo and not parsed.entries:
            log.warning("arxiv feed parse error: %s", parsed.bozo_exception)
            return []

        items: list[Item] = []
        for entry, published in iter_recent_entries(parsed, since):
            try:
                cats = set(_arxiv_categories(entry))
                if cats and not cats.intersection(CATEGORIES):
                    continue
                item = _entry_to_item(entry, published)
                if item is not None:
                    items.append(item)
            except Exception as exc:
                log.warning("arxiv skipped a bad entry: %s", exc)
        return items


ADAPTER = ArxivAdapter()

__all__ = ["ADAPTER", "ArxivAdapter", "CATEGORIES"]
