"""Email delivery via the Resend HTTP API.

`send_email` renders nothing itself — callers pass the already-rendered HTML
(from render_daily_html / render_weekly_html). It POSTs to Resend with the shared
retry helper. When email is not configured (no RESEND_API_KEY / from / to), it is
a no-op that returns False and never raises — render-only mode for tests/CI.

`build_payload` is exposed so tests can assert on the exact Resend request body
without any network.
"""

from __future__ import annotations

import logging

from aidigest.config import Settings, get_settings
from aidigest.ingest.base import make_async_client, with_retry

RESEND_ENDPOINT = "https://api.resend.com/emails"

log = logging.getLogger("aidigest.deliver")


def build_payload(
    *,
    subject: str,
    html: str,
    to: str,
    from_email: str,
) -> dict:
    """Build the Resend `POST /emails` JSON body. Pure — safe to assert in tests."""
    return {
        "from": from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }


async def send_email(
    *,
    subject: str,
    html: str,
    to: str | None = None,
    settings: Settings | None = None,
) -> bool:
    """Send an HTML email via Resend. Returns True on success.

    No-op returning False when email is not configured (render-only). Never
    raises on disabled config; network errors propagate only after retries are
    exhausted.
    """
    cfg = settings or get_settings()
    if not cfg.email_enabled:
        return False

    recipient = to or cfg.digest_to_email
    payload = build_payload(
        subject=subject,
        html=html,
        to=recipient,
        from_email=cfg.digest_from_email,
    )
    headers = {
        "Authorization": f"Bearer {cfg.resend_api_key}",
        "Content-Type": "application/json",
    }

    async def _post() -> bool:
        async with make_async_client() as client:
            resp = await client.post(RESEND_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            return True

    try:
        return await with_retry(_post)
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the pipeline
        log.warning("resend send_email failed: %s", exc)
        return False


__all__ = ["send_email", "build_payload", "RESEND_ENDPOINT"]
