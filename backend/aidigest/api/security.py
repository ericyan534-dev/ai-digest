"""API security helpers — optional API-key auth, signed email links, rate limit.

All three are OFF by default (blank secrets => open) so the localhost dev flow is
unchanged. Set the matching env var to turn each on:

  * AIDIGEST_API_KEY       -> `X-API-Key` required on mutating endpoints.
  * AIDIGEST_LINK_SECRET   -> email feedback links carry an HMAC `sig` that the
                              GET click shim verifies (unforgeable links).
  * AIDIGEST_RATE_LIMIT    -> per-IP fixed-window request cap (default 60/min).

The rate limiter is a single-process in-memory fixed window — correct for the
single-user self-hosted target. Behind multiple workers, put a real limiter
(nginx/Caddy) in front; this stays a cheap backstop.
"""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from aidigest.config import get_settings
from aidigest.deliver.style import feedback_signature


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: enforce `X-API-Key` when AIDIGEST_API_KEY is set."""
    settings = get_settings()
    if not settings.api_auth_enabled:
        return
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    if not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def verify_click_signature(
    *, target_id: str, target_kind: str, signal: str, value: str, sig: str | None
) -> bool:
    """Verify an email feedback link's HMAC. Always True when signing is disabled."""
    settings = get_settings()
    if not settings.link_signing_enabled:
        return True
    expected = feedback_signature(
        target_id=target_id,
        target_kind=target_kind,
        signal=signal,
        value=value,
        secret=settings.feedback_link_secret,
    )
    return sig is not None and secrets.compare_digest(sig, expected)


# Per-IP request timestamps for the fixed-window limiter (process-local).
_HITS: dict[str, deque[float]] = defaultdict(deque)


async def rate_limit(request: Request) -> None:
    """FastAPI dependency: per-IP fixed-window cap (AIDIGEST_RATE_LIMIT/min).

    A plain function (not a callable instance) so FastAPI resolves the special
    ``Request`` type correctly under ``from __future__ import annotations``.
    """
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return
    now = time.monotonic()
    ip = request.client.host if request.client else "unknown"
    window = _HITS[ip]
    while window and now - window[0] > 60.0:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    window.append(now)


__all__ = ["require_api_key", "verify_click_signature", "rate_limit"]
