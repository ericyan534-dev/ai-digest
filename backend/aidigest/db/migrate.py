"""Idempotent schema migration: apply db/schema.sql.

The schema is written with ``CREATE ... IF NOT EXISTS`` everywhere, so applying
it repeatedly is safe. This module is the canonical entrypoint used by
``scripts/migrate.py``.
"""

from __future__ import annotations

import asyncio

from aidigest.db.repo import Repo


async def migrate(dsn: str | None = None) -> None:
    """Connect and apply schema.sql once (idempotent)."""
    repo = Repo(dsn)
    await repo.connect()
    try:
        await repo.init_schema()
    finally:
        await repo.close()


def main() -> None:
    """Synchronous CLI entrypoint."""
    asyncio.run(migrate())


if __name__ == "__main__":
    main()


__all__ = ["migrate", "main"]
