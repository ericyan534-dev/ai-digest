"""Generate today's daily digest. `make daily` -> this.

    python -m scripts.run_daily                  # today, no delivery
    python -m scripts.run_daily --date 2026-06-21
    python -m scripts.run_daily --deliver        # also email/telegram
"""

from __future__ import annotations

import argparse
import asyncio

from aidigest.deliver.render_md import render_daily_md
from aidigest.flows.pipeline import run_daily
from scripts._common import setup_logging


async def _main(date: str | None, deliver: bool) -> None:
    digest = await run_daily(date=date, deliver=deliver)
    print(render_daily_md(digest))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the daily digest.")
    parser.add_argument("--date", default=None, help="ISO date YYYY-MM-DD (default: today)")
    parser.add_argument("--deliver", action="store_true", help="send via email/telegram")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_main(args.date, args.deliver))


if __name__ == "__main__":
    main()
