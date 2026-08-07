"""Generate this week's 'Week at a Glance' editorial. `make weekly` -> this.

    python -m scripts.run_weekly                    # current week
    python -m scripts.run_weekly --week-of 2026-06-15
    python -m scripts.run_weekly --deliver
    python -m scripts.run_weekly --deliver --if-missing  # catch-up; no-op if already shipped
"""

from __future__ import annotations

import argparse
import asyncio

from aidigest.deliver.render_md import render_weekly_md
from aidigest.flows.pipeline import run_weekly, run_weekly_if_missing
from scripts._common import setup_logging


async def _main(week_of: str | None, deliver: bool, if_missing: bool) -> None:
    if if_missing:
        digest = await run_weekly_if_missing(week_of=week_of, deliver=deliver)
        if digest is None:
            # The resolved week is already in the run log (_catch_up logs date=%s
            # on both skip paths); this just needs to be clear to a human reading
            # stdout, so echo back what the caller asked for.
            print(
                f"skipped: {week_of or 'this week'} — weekly digest already shipped "
                "or a run is in flight"
            )
            return
    else:
        digest = await run_weekly(week_of=week_of, deliver=deliver)
    print(render_weekly_md(digest))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the weekly digest.")
    parser.add_argument(
        "--week-of", default=None, help="any ISO date in the target week (default: this week)"
    )
    parser.add_argument("--deliver", action="store_true", help="send via email")
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="only run if this week's digest has not already shipped; for catch-up "
        "after a missed schedule",
    )
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_main(args.week_of, args.deliver, args.if_missing))


if __name__ == "__main__":
    main()
