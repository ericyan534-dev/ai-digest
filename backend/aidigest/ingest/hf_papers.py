"""Hugging Face Daily Papers adapter (JSON API first, RSS fallback).

HF curates a daily list of trending papers with community upvotes — a strong
"this matters" signal that complements raw arXiv. The reliable source is the
JSON API (``/api/daily_papers``); the older ``/papers/rss`` path 401/404s, so it
is only a last-resort fallback. We surface HF upvotes and the arXiv id as metrics
for downstream ranking + Semantic Scholar enrichment. No key required.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from aidigest.ingest._feed import (
    entry_author,
    entry_text,
    fetch_feed,
    iter_recent_entries,
)
from aidigest.ingest._util import after, log, parse_dt
from aidigest.ingest.base import fetch_text
from aidigest.models import Family, Item

# The dependable structured source.
API_URL = "https://huggingface.co/api/daily_papers"
# RSS fallbacks (often broken; tried only if the JSON API yields nothing).
FEED_URLS: tuple[str, ...] = (
    "https://huggingface.co/papers/rss",
    "https://huggingface.co/papers.rss",
    "https://jamesg.blog/feeds/hf-papers.xml",
)
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")
_UPVOTE_RE = re.compile(r"(\d+)\s*(?:upvote|👍|likes?)", re.IGNORECASE)


def _authors(paper: dict) -> str | None:
    authors = paper.get("authors") or []
    names = [str(a.get("name", "")).strip() for a in authors if isinstance(a, dict)]
    joined = ", ".join(n for n in names[:3] if n)
    return joined or None


def _paper_to_item(entry: dict) -> Item | None:
    """Map one HF daily-papers JSON entry to an academia Item."""
    raw_paper = entry.get("paper")
    paper: dict = raw_paper if isinstance(raw_paper, dict) else {}
    title = (paper.get("title") or entry.get("title") or "").strip()
    if not title:
        return None
    arxiv_id = str(paper.get("id") or "").strip() or None
    url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else entry.get("url")
    published = parse_dt(entry.get("publishedAt") or paper.get("publishedAt"))
    body = (paper.get("summary") or "").strip()[:4000]
    upvotes = int(paper.get("upvotes") or 0)
    metrics = {"hf_upvotes": upvotes} if upvotes else {}
    return Item.create(
        source="hf_papers",
        family=Family.ACADEMIA,
        title=title,
        url=url,
        author=_authors(paper),
        published_at=published,
        raw_text=body,
        metrics=metrics,
        raw={"arxiv_id": arxiv_id, "hf_link": url},
    )


# --------------------------------------------------------------------------- #
# RSS fallback (legacy)
# --------------------------------------------------------------------------- #


def _extract_arxiv_id(*texts: str | None) -> str | None:
    for t in texts:
        if t:
            m = _ARXIV_RE.search(t)
            if m:
                return m.group(1)
    return None


def _entry_to_item(entry: Any, published: datetime) -> Item | None:
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    link = entry.get("link") or None
    body = entry_text(entry, max_len=4000)
    arxiv_id = _extract_arxiv_id(link, entry.get("id"), body)
    m = _UPVOTE_RE.search(body or "")
    upvotes = int(m.group(1)) if m else 0
    metrics = {"hf_upvotes": upvotes} if upvotes else {}
    return Item.create(
        source="hf_papers",
        family=Family.ACADEMIA,
        title=title,
        url=link,
        author=entry_author(entry),
        published_at=published,
        raw_text=body,
        metrics=metrics,
        raw={"arxiv_id": arxiv_id, "hf_link": link},
    )


class HFPapersAdapter:
    """HF daily trending papers (JSON API), with HF upvotes + arXiv id as signals."""

    name = "hf_papers"
    family = Family.ACADEMIA

    async def fetch(self, since: datetime) -> list[Item]:
        items = await self._fetch_json(since)
        if items:
            return items
        return await self._fetch_rss(since)

    async def _fetch_json(self, since: datetime) -> list[Item]:
        try:
            data = json.loads(await fetch_text(API_URL))
        except Exception as exc:  # noqa: BLE001 — fall through to RSS
            log.warning("hf_papers JSON API failed: %s", exc)
            return []
        if not isinstance(data, list):
            return []
        items: list[Item] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            try:
                item = _paper_to_item(entry)
                if item is not None and after(item.published_at, since):
                    items.append(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("hf_papers skipped a bad entry: %s", exc)
        return items

    async def _fetch_rss(self, since: datetime) -> list[Item]:
        parsed: Any = None
        for url in FEED_URLS:
            try:
                candidate = await fetch_feed(url)
                if getattr(candidate, "entries", None):
                    parsed = candidate
                    break
            except Exception as exc:  # noqa: BLE001
                log.warning("hf_papers feed %s failed: %s", url, exc)
        if parsed is None:
            return []
        items: list[Item] = []
        for entry, published in iter_recent_entries(parsed, since):
            try:
                item = _entry_to_item(entry, published)
                if item is not None:
                    items.append(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("hf_papers skipped a bad entry: %s", exc)
        return items


ADAPTER = HFPapersAdapter()

__all__ = ["ADAPTER", "HFPapersAdapter"]
