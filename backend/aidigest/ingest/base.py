"""Ingestion adapter contract + shared HTTP/retry helpers.

Every source adapter is a tiny file exposing `name`, `family`, and an async
`fetch(since)` returning normalized `Item`s. Adding a source = adding a ~50-line
file that satisfies the `Adapter` Protocol and registers itself.

Shared helpers (USE THESE — do not roll your own HTTP):
  * make_async_client()  — an httpx.AsyncClient preconfigured with sane timeout.
  * with_retry(...)       — wraps a coroutine fn with tenacity exponential backoff
                            (>= 5 tries) to survive intermittent TLS resets.
  * fetch_text(url)       — GET a URL with retry, return response text.
  * item_id(url, text)    — content-hash id (re-exported from models.content_hash).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from aidigest.config import get_settings
from aidigest.models import Family, Item, content_hash

# Transport-level exceptions worth retrying (TLS resets, timeouts, dropped conns).
RETRYABLE_EXC: tuple[type[BaseException], ...] = (
    httpx.TransportError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    ConnectionError,
)

# Transient HTTP *status* codes (rate-limit + 5xx). 4xx (400/401/403) are NOT
# transient — retrying a bad key/request only wastes time and amplifies load.
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_retryable_status(exc: BaseException) -> bool:
    """True for an httpx.HTTPStatusError whose status code is transient."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _RETRYABLE_STATUS
    )


# --------------------------------------------------------------------------- #
# Adapter Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class Adapter(Protocol):
    """A source adapter. One per source; many small files.

    Attributes:
        name:   stable adapter id, e.g. "hn", "arxiv", "blog:anthropic".
        family: which source world it belongs to.
    """

    name: str
    family: Family

    async def fetch(self, since: datetime) -> list[Item]:
        """Return normalized Items published/updated at-or-after `since`.

        Implementations MUST:
          * use make_async_client() / with_retry() for ALL network calls,
          * build Items via Item.create(...) (content-hash ids),
          * never raise on a single bad record — skip and continue.
        """
        ...


# --------------------------------------------------------------------------- #
# Shared HTTP + retry helpers
# --------------------------------------------------------------------------- #


def make_async_client(**kwargs: object) -> httpx.AsyncClient:
    """Construct an httpx.AsyncClient with the project's default timeout/headers."""
    settings = get_settings()
    defaults: dict = {
        "timeout": httpx.Timeout(settings.http_timeout_seconds),
        "headers": {"User-Agent": "ai-digest/0.1 (+https://github.com/ai-digest)"},
        "follow_redirects": True,
    }
    defaults.update(kwargs)  # type: ignore[arg-type]
    return httpx.AsyncClient(**defaults)  # type: ignore[arg-type]


async def with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int | None = None,
) -> T:
    """Run an async callable with exponential-backoff retry (>= 5 tries).

    Retries on transport/timeout/connection errors. Re-raises the last error
    if all attempts fail.
    """
    settings = get_settings()
    attempts = max_attempts or settings.http_max_retries
    attempts = max(attempts, 5)
    # In offline/mock mode (the test suite) skip the real backoff sleeps so the
    # failure-injection tests don't add minutes; production keeps full backoff.
    wait = (
        wait_exponential(multiplier=0.0, min=0.0, max=0.0)
        if settings.llm_mock
        else wait_exponential(multiplier=0.5, min=0.5, max=20.0)
    )
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait,
        retry=(
            retry_if_exception_type(RETRYABLE_EXC)
            | retry_if_exception(_is_retryable_status)
        ),
        reraise=True,
    ):
        with attempt:
            return await fn()
    raise RuntimeError("unreachable: with_retry exhausted without returning")


async def fetch_text(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    """GET `url` with retry and return response text. Manages its own client if needed."""

    async def _do(c: httpx.AsyncClient) -> str:
        resp = await c.get(url)
        resp.raise_for_status()
        return resp.text

    if client is not None:
        return await with_retry(lambda: _do(client))
    async with make_async_client() as owned:
        return await with_retry(lambda: _do(owned))


def item_id(url: str | None, text: str) -> str:
    """Content-hash id for an Item (re-export of models.content_hash)."""
    return content_hash(url=url, text=text)


__all__ = [
    "Adapter",
    "RETRYABLE_EXC",
    "make_async_client",
    "with_retry",
    "fetch_text",
    "item_id",
]
