"""Apply the database schema (idempotent). `make migrate` -> this.

Connects via the repository and calls `Repo.init_schema()`, which applies
`db/schema.sql` with CREATE ... IF NOT EXISTS (safe to re-run).
"""

from __future__ import annotations

import argparse
import asyncio

from aidigest.db.repo import get_repo
from scripts._common import setup_logging


async def _main() -> None:
    repo = await get_repo()
    await repo.init_schema()
    print("schema applied")


def main() -> None:
    argparse.ArgumentParser(description="Apply ai-digest DB schema (idempotent).").parse_args()
    setup_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
