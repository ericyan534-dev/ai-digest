"""Shared feedparser helpers (RSS/Atom).

Several adapters (rss, smolai, hf_papers, the arXiv RSS fallback) consume RSS or
Atom feeds. This module centralizes: fetching the feed text with retry, parsing
it with feedparser, and mapping entries to normalized dicts the adapters turn
into Items. Robust to missing fields and malformed feeds.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import feedparser

from aidigest.ingest._util import after, html_to_text, log, parse_dt
from aidigest.ingest.base import fetch_text, make_async_client


async def fetch_feed(url: str) -> Any:
    """GET + parse a feed with retry. Returns a feedparser result (never raises)."""
    async with make_async_client(headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}) as client:
        text = await fetch_text(url, client=client)
    parsed = feedparser.parse(text)
    if parsed.bozo and not parsed.entries:
        log.warning("feed %s parsed with error and no entries: %s", url, parsed.bozo_exception)
    return parsed


def entry_dt(entry: Any) -> datetime | None:
    """Best-effort published/updated datetime for a feed entry."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = entry.get(key)
        if struct is not None:
            dt = parse_dt(struct)
            if dt is not None:
                return dt
    for key in ("published", "updated", "created", "date"):
        raw = entry.get(key)
        if raw:
            dt = parse_dt(raw)
            if dt is not None:
                return dt
    return None


def entry_text(entry: Any, *, max_len: int = 4000) -> str:
    """Extract a plain-text body from an entry (content > summary > title)."""
    content = entry.get("content")
    if content and isinstance(content, list):
        raw = content[0].get("value", "")
        if raw:
            return html_to_text(raw, max_len=max_len)
    for key in ("summary", "description", "subtitle"):
        raw = entry.get(key)
        if raw:
            return html_to_text(raw, max_len=max_len)
    return ""


def entry_author(entry: Any) -> str | None:
    """Author name if present."""
    author = entry.get("author")
    if author:
        return str(author)
    authors = entry.get("authors")
    if authors and isinstance(authors, list):
        name = authors[0].get("name")
        if name:
            return str(name)
    return None


def iter_recent_entries(parsed: Any, since: datetime) -> Iterator[tuple[Any, datetime]]:
    """Yield (entry, published_dt) pairs at-or-after `since`, skipping bad rows."""
    entries = getattr(parsed, "entries", None) or []
    for entry in entries:
        try:
            dt = entry_dt(entry)
            if dt is None:
                # No usable date: include it (lab blogs sometimes omit) but mark now-ish.
                # Caller decides; we still want recall. Use a sentinel by skipping the gate.
                yield entry, _fallback_now(since)
                continue
            if after(dt, since):
                yield entry, dt
        except Exception as exc:  # pragma: no cover - defensive per-entry guard
            log.warning("skipping malformed feed entry: %s", exc)


def _fallback_now(since: datetime) -> datetime:
    """When an entry lacks a date, treat it as exactly `since` so it is included once."""
    return since if since.tzinfo else since


__all__ = [
    "entry_author",
    "entry_dt",
    "entry_text",
    "fetch_feed",
    "iter_recent_entries",
]
