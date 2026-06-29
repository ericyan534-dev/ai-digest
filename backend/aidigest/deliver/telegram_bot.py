"""Telegram delivery via the raw Bot API (no SDK dependency).

`send_message` posts MarkdownV2-safe text. `send_daily` posts the daily digest
(rendered to Markdown by render_md) with an inline keyboard of 👍/👎 buttons whose
`callback_data` encodes a feedback signal the bot's webhook can route to
POST /api/feedback.

When Telegram is not configured (no token / chat id), both are no-ops that return
False and never raise — render-only mode for tests/CI. The payload builders
(`build_send_payload`, `daily_keyboard`) are pure so tests can assert offline.
"""

from __future__ import annotations

import logging

from aidigest.config import Settings, get_settings
from aidigest.deliver.render_md import render_telegram_text
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.models import DailyDigest

log = logging.getLogger("aidigest.deliver")

# Telegram MarkdownV2 reserved characters that must be backslash-escaped.
_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"

# callback_data is capped at 64 bytes by Telegram — keep the encoding compact.
_CB_PREFIX = "fb"


def telegram_base_url(token: str) -> str:
    """Base URL for a bot's API calls."""
    return f"https://api.telegram.org/bot{token}"


def escape_md(text: str) -> str:
    """Escape text for Telegram MarkdownV2 parse mode."""
    out: list[str] = []
    for ch in text or "":
        if ch in _MDV2_SPECIAL:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def feedback_callback(*, signal: str, target_id: str, target_kind: str = "digest") -> str:
    """Encode a feedback signal into compact callback_data (<= 64 bytes).

    Format: ``fb:<signal>:<target_kind>:<target_id>``. The bot webhook decodes
    this and POSTs to /api/feedback.
    """
    data = f"{_CB_PREFIX}:{signal}:{target_kind}:{target_id}"
    return data[:64]


def daily_keyboard(digest: DailyDigest) -> dict:
    """Inline keyboard with 👍/👎 buttons mapping to feedback for the digest."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "👍 Useful",
                    "callback_data": feedback_callback(
                        signal="up", target_id=digest.id, target_kind="digest"
                    ),
                },
                {
                    "text": "👎 Meh",
                    "callback_data": feedback_callback(
                        signal="down", target_id=digest.id, target_kind="digest"
                    ),
                },
            ]
        ]
    }


def decode_callback_data(data: str | None) -> tuple[str, str, str] | None:
    """Decode `fb:<signal>:<target_kind>:<target_id>` -> (signal, kind, id).

    Returns None for anything that is not a well-formed feedback callback.
    """
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[0] != _CB_PREFIX:
        return None
    _, signal, target_kind, target_id = parts
    if signal not in ("up", "down") or not target_id:
        return None
    return signal, target_kind, target_id


def extract_feedback_from_update(update: dict) -> tuple[str, str, str, str] | None:
    """Pull (signal, target_kind, target_id, callback_query_id) from a Telegram update.

    Pure — safe to unit-test offline. Returns None when the update is not a
    feedback button press.
    """
    callback = (update or {}).get("callback_query") or {}
    decoded = decode_callback_data(callback.get("data"))
    if decoded is None:
        return None
    signal, target_kind, target_id = decoded
    return signal, target_kind, target_id, str(callback.get("id") or "")


async def answer_callback_query(
    callback_query_id: str, *, text: str = "", settings: Settings | None = None
) -> bool:
    """Acknowledge a button press so the client stops its spinner. No-op when unconfigured."""
    cfg = settings or get_settings()
    if not cfg.telegram_enabled or not callback_query_id:
        return False
    url = f"{telegram_base_url(cfg.telegram_bot_token)}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id, "text": text}

    async def _post() -> bool:
        async with make_async_client() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return bool(resp.json().get("ok", False))

    try:
        return await with_retry(_post)
    except Exception:  # noqa: BLE001 — ack is best-effort; never break the webhook
        return False


def build_send_payload(
    *,
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
    parse_markdown: bool = True,
) -> dict:
    """Build the Telegram `sendMessage` JSON body. Pure — safe to assert in tests.

    ``parse_markdown=True`` escapes for MarkdownV2 (short messages). ``False`` sends
    plain text (no parse_mode) — used for the daily, whose content is too long and
    punctuation-heavy for fragile MarkdownV2 escaping.
    """
    payload: dict = {"chat_id": chat_id, "disable_web_page_preview": True}
    if parse_markdown:
        payload["text"] = escape_md(text)
        payload["parse_mode"] = "MarkdownV2"
    else:
        payload["text"] = text
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return payload


async def _post_send_message(payload: dict, *, token: str) -> bool:
    url = f"{telegram_base_url(token)}/sendMessage"

    async def _post() -> bool:
        async with make_async_client() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()
            return bool(body.get("ok", False))

    return await with_retry(_post)


async def send_message(text: str, *, settings: Settings | None = None) -> bool:
    """Send a Markdown message via the Telegram Bot API. Returns True on success.

    No-op returning False when Telegram is not configured (render-only).
    """
    cfg = settings or get_settings()
    if not cfg.telegram_enabled:
        return False
    payload = build_send_payload(chat_id=cfg.telegram_chat_id, text=text)
    return await _post_send_message(payload, token=cfg.telegram_bot_token)


async def send_daily(digest: DailyDigest, *, settings: Settings | None = None) -> bool:
    """Send the daily digest with inline 👍/👎 buttons. Returns True on success.

    No-op returning False when Telegram is not configured (render-only). The
    digest is always rendered (so callers/tests can inspect it) before the send
    decision is made.
    """
    cfg = settings or get_settings()
    text = render_telegram_text(digest)  # condensed PLAIN text (push channel)
    if not cfg.telegram_enabled:
        return False
    payload = build_send_payload(
        chat_id=cfg.telegram_chat_id,
        text=text,
        reply_markup=daily_keyboard(digest),
        parse_markdown=False,
    )
    try:
        return await _post_send_message(payload, token=cfg.telegram_bot_token)
    except Exception as exc:  # noqa: BLE001 — delivery is best-effort, never fatal
        log.warning("telegram send_daily failed: %s", exc)
        return False


__all__ = [
    "send_message",
    "send_daily",
    "build_send_payload",
    "daily_keyboard",
    "feedback_callback",
    "decode_callback_data",
    "extract_feedback_from_update",
    "answer_callback_query",
    "escape_md",
    "telegram_base_url",
]
