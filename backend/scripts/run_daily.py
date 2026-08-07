"""Generate today's daily digest. `make daily` -> this.

    python -m scripts.run_daily                  # today, no delivery
    python -m scripts.run_daily --date 2026-06-21
    python -m scripts.run_daily --deliver        # also email/telegram
    python -m scripts.run_daily --deliver --if-missing  # catch-up; no-op if already shipped
"""

from __future__ import annotations

import argparse
import asyncio

from aidigest.deliver.render_md import render_daily_md
from aidigest.flows.pipeline import run_daily, run_daily_if_missing
from scripts._common import setup_logging


async def _main(date: str | None, deliver: bool, if_missing: bool) -> None:
    if if_missing:
        digest = await run_daily_if_missing(date=date, deliver=deliver)
        if digest is None:
            # The resolved date is already in the run log (_catch_up logs date=%s
            # on both skip paths); this just needs to be clear to a human reading
            # stdout, so echo back what the caller asked for.
            print(
                f"skipped: {date or 'today'} — daily digest already shipped "
                "or a run is in flight"
            )
            return
    else:
        digest = await run_daily(date=date, deliver=deliver)
    print(render_daily_md(digest))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the daily digest.")
    parser.add_argument("--date", default=None, help="ISO date YYYY-MM-DD (default: today)")
    parser.add_argument("--deliver", action="store_true", help="send via email/telegram")
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="only run if this date's digest has not already shipped; for catch-up "
        "after a missed schedule",
    )
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_main(args.date, args.deliver, args.if_missing))


if __name__ == "__main__":
    main()
