"""Hacker News adapter (Algolia API) — AI-relevant front-page + Show HN.

Uses the free, no-auth HN Search API (Algolia). We pull recent front-page-ish
stories (by points) and Show HN posts, filter to AI-relevant titles, and map to
community Items carrying upvotes/comments metrics.

API docs: https://hn.algolia.com/api  (search_by_date + numericFilters).
No key required; respectful single-shot queries with retry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aidigest.ingest._util import after, is_ai_relevant, log, parse_dt
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.models import Family, Item

_ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
_MIN_POINTS = 20  # front-page floor; Show HN is included regardless via tag
_HITS_PER_PAGE = 100


async def _query(client: Any, *, tags: str, since_ts: int, numeric: str | None) -> list[dict]:
    params: dict[str, str | int] = {
        "tags": tags,
        "hitsPerPage": _HITS_PER_PAGE,
    }
    if numeric:
        params["numericFilters"] = f"created_at_i>{since_ts},{numeric}"
    else:
        params["numericFilters"] = f"created_at_i>{since_ts}"

    async def _do() -> list[dict]:
        resp = await client.get(_ALGOLIA, params=params)
        resp.raise_for_status()
        data = resp.json()
        return list(data.get("hits", []))

    return await with_retry(_do)


def _hit_to_item(hit: dict, *, family: Family) -> Item | None:
    title = (hit.get("title") or hit.get("story_title") or "").strip()
    if not title:
        return None
    object_id = hit.get("objectID")
    story_url = hit.get("url")
    hn_url = f"https://news.ycombinator.com/item?id={object_id}" if object_id else None
    url = story_url or hn_url
    published = parse_dt(hit.get("created_at_i")) or parse_dt(hit.get("created_at"))
    metrics = {
        "upvotes": int(hit.get("points") or 0),
        "comments": int(hit.get("num_comments") or 0),
    }
    body = (hit.get("story_text") or hit.get("comment_text") or "").strip()
    return Item.create(
        source="hn",
        family=family,
        title=title,
        url=url,
        author=hit.get("author"),
        published_at=published,
        raw_text=body,
        metrics=metrics,
        raw={"hn_id": object_id, "hn_url": hn_url},
    )


class HackerNewsAdapter:
    """AI-relevant HN stories (front page by points) + all Show HN posts."""

    name = "hn"
    family = Family.COMMUNITY

    async def fetch(self, since: datetime) -> list[Item]:
        since_ts = int(since.timestamp())
        try:
            async with make_async_client() as client:
                front = await _query(
                    client,
                    tags="story",
                    since_ts=since_ts,
                    numeric=f"points>={_MIN_POINTS}",
                )
                show = await _query(
                    client,
                    tags="show_hn",
                    since_ts=since_ts,
                    numeric=None,
                )
        except Exception as exc:
            log.warning("hn adapter failed: %s", exc)
            return []

        seen: set[str] = set()
        items: list[Item] = []
        for hit in [*front, *show]:
            try:
                oid = str(hit.get("objectID") or "")
                if oid and oid in seen:
                    continue
                seen.add(oid)
                title = hit.get("title") or hit.get("story_title") or ""
                is_show = "show_hn" in (hit.get("_tags") or [])
                if not is_show and not is_ai_relevant(title, hit.get("url")):
                    continue
                item = _hit_to_item(hit, family=self.family)
                if item is None:
                    continue
                if not after(item.published_at, since):
                    continue
                items.append(item)
            except Exception as exc:
                log.warning("hn adapter skipped a bad hit: %s", exc)
        return items


ADAPTER = HackerNewsAdapter()

__all__ = ["ADAPTER", "HackerNewsAdapter"]
