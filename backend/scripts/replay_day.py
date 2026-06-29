"""Replay a SPECIFIC historical day through the pipeline (validation harness).

Pulls that day's Hacker News stories (HN Algolia, date-ranged, with real
points/comments so engagement detection works) plus a small current academia +
industry sample for structure, then runs embed -> cluster -> rank -> curate ->
classify_day_tier -> generate_daily -> render. Use it to verify the system
DETECTS a real breakthrough day (e.g. Jun 09 'Claude Fable 5') as BREAKTHROUGH
and leads with full depth.

    python -m scripts.replay_day --date 2026-06-09
    python -m scripts.replay_day --date 2026-06-25   # a quieter day
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from aidigest.config import get_settings
from aidigest.deliver.render_md import render_daily_md
from aidigest.generate.daily import generate_daily
from aidigest.generate.importance import classify_day_tier
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.llm.factory import get_llm
from aidigest.models import Family, Item
from aidigest.personalize.profile import build_interest_vector, load_profile
from aidigest.process.cluster import cluster_into_stories
from aidigest.process.curate import curate_stories
from aidigest.process.embed import embed_items
from aidigest.process.rank import score_stories

_HN = "https://hn.algolia.com/api/v1/search"


async def _hn_for_day(date: str, *, min_points: int = 40, limit: int = 60) -> list[Item]:
    d = datetime.fromisoformat(date).replace(tzinfo=UTC)
    start, end = int(d.timestamp()), int((d + timedelta(days=1)).timestamp())
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{start},created_at_i<{end}",
        "hitsPerPage": limit,
    }

    async def _do() -> list[dict]:
        async with make_async_client() as c:
            r = await c.get(_HN, params=params)
            r.raise_for_status()
            return list(r.json().get("hits", []))

    hits = await with_retry(_do)
    items: list[Item] = []
    for h in hits:
        if (h.get("points") or 0) < min_points or not (h.get("title") or "").strip():
            continue
        oid = h.get("objectID")
        items.append(
            Item.create(
                source="hn",
                family=Family.COMMUNITY,
                title=h["title"].strip(),
                url=h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                author=h.get("author"),
                published_at=datetime.fromtimestamp(h.get("created_at_i", start), UTC),
                raw_text=(h.get("story_text") or "")[:2000],
                metrics={"upvotes": h.get("points") or 0, "comments": h.get("num_comments") or 0},
                raw={"hn_id": oid},
            )
        )
    return items


async def _current_structure(since: datetime) -> list[Item]:
    """A small current academia + industry sample so the digest has all sections."""
    from aidigest.ingest.arxiv import ADAPTER as ARXIV
    from aidigest.ingest.feeds import FEEDS
    from aidigest.ingest.hf_papers import ADAPTER as HF
    from aidigest.ingest.rss import RSSAdapter

    out: list[Item] = []
    for adapter in (ARXIV, HF):
        try:
            out += (await adapter.fetch(since))[:14]
        except Exception as exc:  # noqa: BLE001
            print(f"# {adapter.name} failed: {exc}", file=sys.stderr)
    for slug, url, fam in FEEDS:
        if fam == Family.INDUSTRY:
            try:
                out += (await RSSAdapter(name=slug, url=url, family=fam).fetch(since))[:5]
            except Exception:  # noqa: BLE001
                pass
    return out


async def _run(date: str) -> None:
    if get_settings().llm_mock:
        print("ERROR: replay_day is LIVE. Set AIDIGEST_LLM_MOCK=0 + GEMINI_API_KEY.", file=sys.stderr)
        raise SystemExit(2)
    profile = load_profile()
    llm = get_llm()

    hn = await _hn_for_day(date)
    structure = await _current_structure(datetime.now(UTC) - timedelta(hours=72))
    items = hn + structure
    print(f"# replay {date}: hn={len(hn)} +structure={len(structure)} = {len(items)} items", file=sys.stderr)
    if not items:
        print("no items", file=sys.stderr)
        return

    items = await embed_items(items, llm=llm)
    threshold = float((profile.get("processing") or {}).get("cluster_threshold", 0.86))
    stories = cluster_into_stories(items, threshold=threshold)
    interest = await build_interest_vector(profile, llm=llm)
    stories = score_stories(stories, interest_vector=interest, profile=profile)
    top_imp = max((s.importance for s in stories), default=0.0)
    stories = await curate_stories(stories, profile=profile, llm=llm)
    day = classify_day_tier(stories, profile=profile)
    print(f"# DAY TIER = {day.value}  (max importance={top_imp:.3f}, curated={len(stories)})", file=sys.stderr)

    items_by_id = {it.id: it for it in items}
    gen_date = datetime.fromisoformat(date).date().isoformat()
    digest = await generate_daily(stories, items_by_id, profile=profile, date=gen_date, llm=llm)
    print(render_daily_md(digest))


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a historical day through the pipeline.")
    ap.add_argument("--date", required=True, help="ISO date YYYY-MM-DD to replay")
    args = ap.parse_args()
    from scripts._common import setup_logging

    setup_logging("WARNING")
    asyncio.run(_run(args.date))


if __name__ == "__main__":
    main()
