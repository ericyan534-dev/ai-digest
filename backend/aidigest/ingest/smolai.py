"""smol.ai / AINews adapter (meta source).

news.smol.ai is the daily AI-news digest this engine is modeled on. We treat its
issues as a high-signal *meta* source: each issue becomes one Item summarizing
"what the rest of the field thinks mattered today." Also doubles as an X-proxy
(viral X content resurfaces in smol.ai within hours).

We read the public RSS feed; if the primary path 404s we try alternates. Robust
to missing fields and network errors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aidigest.ingest._feed import (
    entry_author,
    entry_text,
    fetch_feed,
    iter_recent_entries,
)
from aidigest.ingest._util import log
from aidigest.models import Family, Item

FEED_URLS: tuple[str, ...] = (
    "https://news.smol.ai/rss.xml",
    "https://news.smol.ai/feed.xml",
    "https://buttondown.com/ainews/rss",
)


def _entry_to_item(entry: Any, published: datetime) -> Item | None:
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    link = entry.get("link") or None
    body = entry_text(entry, max_len=8000)
    return Item.create(
        source="smol.ai",
        family=Family.META,
        title=title,
        url=link,
        author=entry_author(entry) or "smol.ai",
        published_at=published,
        raw_text=body,
        metrics={},
        raw={"issue_link": link},
    )


class SmolAIAdapter:
    """smol.ai / AINews daily issues as a meta digest source."""

    name = "smol.ai"
    family = Family.META

    async def fetch(self, since: datetime) -> list[Item]:
        parsed: Any = None
        for url in FEED_URLS:
            try:
                candidate = await fetch_feed(url)
                if getattr(candidate, "entries", None):
                    parsed = candidate
                    break
            except Exception as exc:
                log.warning("smol.ai feed %s failed: %s", url, exc)
        if parsed is None:
            return []

        items: list[Item] = []
        for entry, published in iter_recent_entries(parsed, since):
            try:
                item = _entry_to_item(entry, published)
                if item is not None:
                    items.append(item)
            except Exception as exc:
                log.warning("smol.ai skipped a bad entry: %s", exc)
        return items


ADAPTER = SmolAIAdapter()

__all__ = ["ADAPTER", "SmolAIAdapter"]
