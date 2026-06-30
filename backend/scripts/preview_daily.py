"""Generate a daily digest from a LIVE in-memory run (NO database) and optionally
deliver it. Useful before a DB is provisioned: pulls real sources, embeds,
clusters, ranks, classifies, generates with the real Gemini client, prints the
Markdown, and (with --deliver) emails + Telegrams it.

    python -m scripts.preview_daily --hours 48 --max-items 36
    python -m scripts.preview_daily --deliver
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aidigest.config import get_settings
from aidigest.deliver.email_resend import send_email
from aidigest.deliver.render_md import render_daily_html, render_daily_md
from aidigest.deliver.telegram_bot import send_daily as tg_send_daily
from aidigest.generate.daily import generate_daily
from aidigest.generate.importance import classify_day
from aidigest.ingest.registry import ingest_all
from aidigest.llm.factory import get_llm
from aidigest.models import Family, Item
from aidigest.personalize.profile import build_interest_vector, load_profile
from aidigest.process.cluster import cluster_into_stories
from aidigest.process.curate import curate_stories
from aidigest.process.embed import embed_items
from aidigest.process.rank import apply_announcement_floor, score_stories
from scripts._common import setup_logging

# Academia (arXiv / HF papers) gets a WIDER lookback than news. A paper's date is its
# original submission, not a "today" announcement, and HF's daily-trending list is
# routinely a few days old — so a strict ~30h news window empties the research section,
# especially on weekends when arXiv announces nothing new. News stays strict for date
# accuracy (no stale announcements leaking into the wrong day); academia reaches back
# far enough to always have material for the research-trends recap.
_ACADEMIA_LOOKBACK_HOURS = 96


def _balanced_pool(items: list, *, per_source: int) -> list:
    """Cap EACH source to its most-recent `per_source` items so high-volume sources
    (arXiv, HN) don't crowd academia/industry out of the embed pool."""
    from collections import defaultdict

    by_src: dict[str, list] = defaultdict(list)
    for it in sorted(items, key=lambda x: x.published_at, reverse=True):
        if len(by_src[it.source]) < per_source:
            by_src[it.source].append(it)
    return [it for lst in by_src.values() for it in lst]


def _family_counts(stories: list) -> str:
    from collections import Counter

    counts = Counter(s.family.value for s in stories)
    return " ".join(f"{k}={v}" for k, v in counts.most_common())


async def _run(hours: int, per_source: int, deliver: bool) -> None:
    settings = get_settings()
    profile = load_profile()
    llm = get_llm()

    now = datetime.now(UTC)
    since_news = now - timedelta(hours=hours)
    since_acad = now - timedelta(hours=max(hours, _ACADEMIA_LOOKBACK_HOURS))

    # A DAILY only covers items PUBLISHED in-window — never older pieces (a Jun-24
    # announcement must not appear in a Jun-26 daily). News uses the strict window;
    # academia uses the wider one so the research section isn't empty on weekends.
    # The explicit filter also drops any dateless/fallback item that slipped past the
    # adapter's `since` gate.
    def _in_window(it: Item) -> bool:
        bound = since_acad if it.family == Family.ACADEMIA else since_news
        return it.published_at >= bound

    fresh = [it for it in await ingest_all(since_acad) if _in_window(it)]
    items = _balanced_pool(fresh, per_source=per_source)
    print(f"# balanced pool items={len(items)} (window={hours}h, <= {per_source}/source)", file=sys.stderr)
    if not items:
        print("no items ingested — nothing to do")
        return

    items = await embed_items(items, llm=llm)
    threshold = float((profile.get("processing") or {}).get("cluster_threshold", 0.86))
    stories = cluster_into_stories(items, threshold=threshold)
    interest = await build_interest_vector(profile, llm=llm)
    stories = score_stories(stories, interest_vector=interest, profile=profile)
    before = len(stories)
    stories = await curate_stories(stories, profile=profile, llm=llm)
    stories = apply_announcement_floor(stories)  # real announcements register as notable
    print(f"# curated: {before} -> {len(stories)} worthy stories  ({_family_counts(stories)})", file=sys.stderr)
    stories, _overall, quiet = classify_day(stories, profile=profile)

    items_by_id = {it.id: it for it in items}
    date = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    # generate_daily does the balanced per-family selection (academia/industry/community).
    digest = await generate_daily(stories, items_by_id, profile=profile, date=date, llm=llm)

    print(f"# quiet_day={quiet}  tier={digest.overall_tier.value}  sections={len(digest.sections)}", file=sys.stderr)
    print(render_daily_md(digest))

    if deliver:
        html = render_daily_html(
            digest, api_base=settings.public_base_url, link_secret=settings.feedback_link_secret
        )
        emailed = await send_email(subject=f"AI Digest — {digest.date}", html=html)
        telegrammed = await tg_send_daily(digest)
        print(f"# delivered: email={emailed} telegram={telegrammed}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Live in-memory daily digest (no DB).")
    ap.add_argument(
        "--hours", type=int, default=30,
        help="daily window in hours (~last day; >48 would mix in older days)",
    )
    ap.add_argument(
        "--per-source", type=int, default=18,
        help="cap each source to its N most-recent items (balances the embed pool)",
    )
    ap.add_argument("--deliver", action="store_true", help="email + Telegram the result")
    args = ap.parse_args()
    setup_logging("WARNING")
    asyncio.run(_run(args.hours, args.per_source, args.deliver))


if __name__ == "__main__":
    main()
