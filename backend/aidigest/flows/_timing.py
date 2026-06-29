"""Structured logging + step-timing helpers for the pipeline.

Keeps `pipeline.py` focused on orchestration. `step()` is an async context
manager that logs start/finish with elapsed seconds in a structured, grep-able
key=value format, and re-raises (with timing) on failure.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger("aidigest.flows")


class StepTimer:
    """Mutable result handle a `step()` block can annotate (e.g. count written)."""

    __slots__ = ("name", "extra")

    def __init__(self, name: str) -> None:
        self.name = name
        self.extra: dict[str, object] = {}

    def set(self, **fields: object) -> None:
        """Attach structured fields surfaced in the completion log line."""
        self.extra.update(fields)


def _fmt(fields: dict[str, object]) -> str:
    return " ".join(f"{k}={v}" for k, v in fields.items())


@asynccontextmanager
async def step(name: str) -> AsyncIterator[StepTimer]:
    """Time a pipeline step, logging structured start/ok/error lines.

    Usage:
        async with step("ingest") as s:
            n = await do_work()
            s.set(items=n)
    """
    timer = StepTimer(name)
    started = time.perf_counter()
    logger.info("step=%s status=start", name)
    try:
        yield timer
    except Exception as exc:  # noqa: BLE001 — log timing then re-raise to caller
        elapsed = time.perf_counter() - started
        logger.error(
            "step=%s status=error elapsed_s=%.3f error=%r %s",
            name,
            elapsed,
            exc,
            _fmt(timer.extra),
        )
        raise
    else:
        elapsed = time.perf_counter() - started
        logger.info(
            "step=%s status=ok elapsed_s=%.3f %s",
            name,
            elapsed,
            _fmt(timer.extra),
        )


__all__ = ["StepTimer", "step", "logger"]
