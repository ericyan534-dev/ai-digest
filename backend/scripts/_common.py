"""Shared helpers for the thin CLI entrypoints in `scripts/`.

Keeps each script tiny: configure structured logging once, then call a single
pipeline coroutine. Run from inside `backend/` (e.g. `python -m scripts.run_daily`).
"""

from __future__ import annotations

import logging
import os


def setup_logging(level: str | None = None) -> None:
    """Configure root logging with a compact, grep-able structured format."""
    raw = level or os.environ.get("AIDIGEST_LOG_LEVEL") or "INFO"
    lvl = raw.upper()
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


__all__ = ["setup_logging"]
