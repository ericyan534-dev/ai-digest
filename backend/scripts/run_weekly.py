"""Generate this week's 'Week at a Glance' editorial. `make weekly` -> this.

    python -m scripts.run_weekly                    # current week
    python -m scripts.run_weekly --week-of 2026-06-15
    python -m scripts.run_weekly --deliver
"""

from __future__ import annotations

import argparse
import asyncio

from aidigest.deliver.render_md import render_weekly_md
from aidigest.flows.pipeline import run_weekly
from scripts._common import setup_logging


async def _main(week_of: str | None, deliver: bool) -> None:
    digest = await run_weekly(week_of=week_of, deliver=deliver)
    print(render_weekly_md(digest))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the weekly digest.")
    parser.add_argument(
        "--week-of", default=None, help="any ISO date in the target week (default: this week)"
    )
    parser.add_argument("--deliver", action="store_true", help="send via email")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_main(args.week_of, args.deliver))


if __name__ == "__main__":
    main()
