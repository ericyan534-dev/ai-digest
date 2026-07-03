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
import re

from aidigest.config import Settings, get_settings
from aidigest.ingest.base import make_async_client, with_retry

RESEND_ENDPOINT = "https://api.resend.com/emails"
_SENDER_NAME = "AI Digest"

log = logging.getLogger("aidigest.deliver")


def _addr(from_email: str) -> str:
    """Bare address from either 'Name <a@b.com>' or 'a@b.com'."""
    m = re.search(r"<([^>]+)>", from_email)
    return (m.group(1) if m else from_email).strip()


def _html_to_text(html: str) -> str:
    """Crude HTML->text fallback for the plain-text alternative part."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_payload(
    *,
    subject: str,
    html: str,
    to: str,
    from_email: str,
    text: str | None = None,
) -> dict:
    """Build the Resend `POST /emails` JSON body. Pure — safe to assert in tests.

    Hardened for Gmail deliverability: a From DISPLAY NAME, a plain-text
    alternative (HTML-only mail is a strong spam signal), a List-Unsubscribe
    header (expected by Gmail's 2024 sender rules), and a Reply-To. These push
    the digest toward the inbox instead of spam.
    """
    addr = _addr(from_email)
    display_from = from_email if "<" in from_email else f"{_SENDER_NAME} <{addr}>"
    return {
        "from": display_from,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": (text or _html_to_text(html)) or subject,
        "reply_to": addr,
        "headers": {
            "List-Unsubscribe": f"<mailto:{addr}?subject=unsubscribe>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }


async def send_email(
    *,
    subject: str,
    html: str,
    text: str | None = None,
    to: str | None = None,
    settings: Settings | None = None,
) -> bool:
    """Send an HTML email via Resend. Returns True on success.

    ``text`` is the plain-text alternative (pass the markdown render); when omitted
    it is derived from the HTML. No-op returning False when email is not configured
    (render-only). Never raises on disabled config; network errors propagate only
    after retries are exhausted.
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
        text=text,
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
