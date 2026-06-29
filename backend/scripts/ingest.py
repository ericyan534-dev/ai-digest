"""Run ingestion only (fetch sources -> upsert items). `make` / cron entrypoint.

    python -m scripts.ingest            # last 36h
    python -m scripts.ingest --hours 72
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from aidigest.flows.pipeline import run_ingest
from scripts._common import setup_logging


async def _main(hours: int) -> None:
    since = datetime.now(UTC) - timedelta(hours=hours)
    written = await run_ingest(since=since)
    print(f"ingested items written={written}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest sources into the items table.")
    parser.add_argument("--hours", type=int, default=36, help="look-back window in hours")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_main(args.hours))


if __name__ == "__main__":
    main()
