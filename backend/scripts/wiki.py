"""Export the latest daily + weekly digests to the Karpathy-wiki dir (`make wiki`).

Pulls the most recent daily and weekly digests from the DB and writes them as
linked Obsidian-style Markdown notes. Dir resolution: --dir > AIDIGEST_WIKI_DIR
> ./wiki.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from aidigest.config import get_settings
from aidigest.db.repo import get_repo
from aidigest.deliver.wiki_export import export_daily, export_weekly
from aidigest.models import DailyDigest, DigestKind, WeeklyDigest
from scripts._common import setup_logging


async def _main(wiki_dir: str) -> None:
    repo = await get_repo()
    written = 0

    daily_rows = await repo.list_digests(kind=DigestKind.DAILY, limit=1)
    if daily_rows:
        d = await repo.get_digest(daily_rows[0]["id"])
        if isinstance(d, DailyDigest):
            written += len(export_daily(d, wiki_dir=wiki_dir))

    weekly_rows = await repo.list_digests(kind=DigestKind.WEEKLY, limit=1)
    if weekly_rows:
        w = await repo.get_digest(weekly_rows[0]["id"])
        if isinstance(w, WeeklyDigest):
            start = date.fromisoformat(w.week_of)
            dates = [(start + timedelta(days=i)).isoformat() for i in range(7)]
            written += len(export_weekly(w, wiki_dir=wiki_dir, daily_dates=dates))

    await repo.close()
    print(f"wiki: wrote {written} notes to {wiki_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export digests to the Karpathy-wiki dir.")
    ap.add_argument("--dir", default=None, help="wiki dir (default: AIDIGEST_WIKI_DIR or ./wiki)")
    args = ap.parse_args()
    setup_logging()
    wiki_dir = args.dir or get_settings().wiki_dir or "./wiki"
    asyncio.run(_main(wiki_dir))


if __name__ == "__main__":
    main()
