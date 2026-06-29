"""Adapter registry — the one place that knows every source.

`all_adapters()` assembles the singleton adapters (hn/reddit/arxiv/openreview/
semantic_scholar/hf_papers/smol.ai) plus one `RSSAdapter` per row in `FEEDS`.
`ingest_all()` runs them all concurrently, isolates per-adapter failures, and
returns a de-duplicated-by-id list of Items — never crashing the batch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from functools import lru_cache

from aidigest.ingest._util import log
from aidigest.ingest.arxiv import ADAPTER as ARXIV
from aidigest.ingest.base import Adapter
from aidigest.ingest.feeds import FEEDS
from aidigest.ingest.hf_papers import ADAPTER as HF_PAPERS
from aidigest.ingest.hn import ADAPTER as HN
from aidigest.ingest.openreview import ADAPTER as OPENREVIEW
from aidigest.ingest.reddit import ADAPTER as REDDIT
from aidigest.ingest.rss import RSSAdapter
from aidigest.ingest.semantic_scholar import ADAPTER as SEMANTIC_SCHOLAR
from aidigest.ingest.smolai import ADAPTER as SMOLAI
from aidigest.models import Item

# The fixed, non-RSS adapters (single instance each).
_CORE_ADAPTERS: tuple[Adapter, ...] = (
    HN,
    REDDIT,
    ARXIV,
    OPENREVIEW,
    SEMANTIC_SCHOLAR,
    HF_PAPERS,
    SMOLAI,
)


@lru_cache(maxsize=1)
def all_adapters() -> list[Adapter]:
    """Return every enabled adapter instance (core + one RSSAdapter per FEEDS row)."""
    rss_adapters: list[Adapter] = [
        RSSAdapter(name=slug, url=url, family=family) for slug, url, family in FEEDS
    ]
    return [*_CORE_ADAPTERS, *rss_adapters]


def get_adapter(name: str) -> Adapter | None:
    """Look up a single adapter by its `name` (e.g. 'hn', 'rss:openai')."""
    for adapter in all_adapters():
        if adapter.name == name:
            return adapter
    return None


async def _run_one(adapter: Adapter, since: datetime) -> list[Item]:
    """Run one adapter, isolating any exception into an empty result + log."""
    try:
        items = await adapter.fetch(since)
        log.info("adapter %s -> %d items", adapter.name, len(items))
        return items
    except Exception as exc:  # defensive: fetch should already guard, double-safe.
        log.warning("adapter %s crashed (isolated): %s", adapter.name, exc)
        return []


async def ingest_all(
    since: datetime, *, adapters: list[Adapter] | None = None
) -> list[Item]:
    """Run all adapters concurrently; return combined, de-duplicated-by-id Items.

    Exceptions are isolated per adapter (return_exceptions=True semantics via
    `_run_one`), so one failing source never aborts the run.
    """
    chosen = adapters if adapters is not None else all_adapters()
    if not chosen:
        return []
    results = await asyncio.gather(
        *(_run_one(a, since) for a in chosen), return_exceptions=False
    )

    seen: set[str] = set()
    combined: list[Item] = []
    for batch in results:
        for item in batch:
            if item.id in seen:
                continue
            seen.add(item.id)
            combined.append(item)
    log.info("ingest_all: %d unique items from %d adapters", len(combined), len(chosen))
    return combined


__all__ = ["all_adapters", "get_adapter", "ingest_all"]
