"""Nightly maintenance: recompute the interest vector + grade the latest daily.

    python -m scripts.run_nightly
"""

from __future__ import annotations

import argparse
import asyncio

from aidigest.flows.pipeline import run_nightly
from scripts._common import setup_logging


async def _main() -> None:
    await run_nightly()
    print("nightly maintenance complete")


def main() -> None:
    argparse.ArgumentParser(description="Run nightly maintenance (Loop 2 + eval).").parse_args()
    setup_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
