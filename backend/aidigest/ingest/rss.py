"""Generic RSS/Atom adapter (lab blogs + top newsletters).

One `RSSAdapter` instance per feed; the registry builds many from `FEEDS`. Lab
blogs (OpenAI/Anthropic/DeepMind/Mistral/Qwen) are `Family.INDUSTRY`; curator
newsletters (Latent Space/Import AI/Interconnects/The Batch) are `Family.META`.

`name` is `"rss:<slug>"`. Robust to missing dates/fields and network errors.
"""

from __future__ import annotations

from datetime import datetime

from aidigest.ingest._feed import (
    entry_author,
    entry_text,
    fetch_feed,
    iter_recent_entries,
)
from aidigest.ingest._reader import fetch_readable
from aidigest.ingest._util import log
from aidigest.models import Family, Item


class RSSAdapter:
    """Adapter over a single RSS/Atom feed (parameterized; see registry FEEDS)."""

    def __init__(self, *, name: str, url: str, family: Family) -> None:
        self.name = name if name.startswith("rss:") else f"rss:{name}"
        self.url = url
        self.family = family

    async def fetch(self, since: datetime) -> list[Item]:
        """Return AI feed entries published at-or-after `since`. Never raises."""
        try:
            parsed = await fetch_feed(self.url)
        except Exception as exc:
            log.warning("rss adapter %s failed to fetch %s: %s", self.name, self.url, exc)
            return []

        items: list[Item] = []
        for entry, published in iter_recent_entries(parsed, since):
            try:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                url = entry.get("link") or None
                body = entry_text(entry)
                # Lab-blog RSS often carries only a lead sentence; fetch the full
                # readable page when the body is too thin (no-op offline/mock).
                if self.family == Family.INDUSTRY:
                    body = await fetch_readable(url, body)
                items.append(
                    Item.create(
                        source=self.name,
                        family=self.family,
                        title=title,
                        url=url,
                        author=entry_author(entry),
                        published_at=published,
                        raw_text=body,
                        raw={"feed": self.url},
                    )
                )
            except Exception as exc:
                log.warning("rss adapter %s skipped a bad entry: %s", self.name, exc)
        return items


# A single default instance so the module satisfies the "export ADAPTER" rule.
# Real fan-out over many feeds happens in the registry via FEEDS.
ADAPTER = RSSAdapter(
    name="rss:default",
    url="https://news.smol.ai/rss.xml",
    family=Family.META,
)

__all__ = ["ADAPTER", "RSSAdapter"]
