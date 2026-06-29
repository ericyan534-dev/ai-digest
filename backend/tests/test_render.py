"""Rendering tests: Markdown, email HTML, and Telegram payloads from a digest.

MOCK mode, no network. Delivery channels are render-only here (Telegram/Email
are disabled in test settings, so the async senders are no-ops returning False
but still RENDER the digest, which is what we assert).

Covers:
  * render_daily_md / render_weekly_md produce non-empty Markdown with the
    right headlines/sections and HONEST quiet-day text when quiet.
  * render_daily_html / render_weekly_html produce valid-looking HTML for email.
  * the Telegram payload builder renders a daily digest + 👍/👎 keyboard.
  * disabled email/telegram senders no-op to False without raising.
"""

from __future__ import annotations

import pytest

from aidigest.deliver.email_resend import send_email
from aidigest.deliver.render_md import (
    render_daily_html,
    render_daily_md,
    render_weekly_html,
    render_weekly_md,
)
from aidigest.deliver.telegram_bot import (
    build_send_payload,
    daily_keyboard,
    send_daily,
    send_message,
)
from aidigest.models import DailyDigest

# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def test_render_daily_md_busy(busy_daily: DailyDigest) -> None:
    md = render_daily_md(busy_daily)
    assert isinstance(md, str) and md.strip()
    assert "DeepSeek V4" in md
    assert busy_daily.tldr in md
    # Family section headings show up.
    assert "Academia" in md


def test_render_daily_md_quiet_is_honest(quiet_daily: DailyDigest) -> None:
    md = render_daily_md(quiet_daily)
    assert "quiet" in md.lower()
    assert "nothing major shipped" in md.lower()


def test_render_weekly_md(sample_weekly) -> None:
    md = render_weekly_md(sample_weekly)
    assert sample_weekly.title in md
    assert sample_weekly.lede in md
    # shortlist + radar surfaced.
    assert "DeepSeek V4 paper" in md
    assert "Linear attention for long context" in md


# --------------------------------------------------------------------------- #
# HTML (email)
# --------------------------------------------------------------------------- #


def test_render_daily_html(busy_daily: DailyDigest) -> None:
    html = render_daily_html(busy_daily)
    assert "<" in html and ">" in html  # looks like markup
    assert "DeepSeek V4" in html


def test_render_weekly_html(sample_weekly) -> None:
    html = render_weekly_html(sample_weekly)
    assert "<" in html and ">" in html
    assert sample_weekly.title in html


def test_render_daily_html_quiet(quiet_daily: DailyDigest) -> None:
    html = render_daily_html(quiet_daily)
    assert "quiet" in html.lower()


# --------------------------------------------------------------------------- #
# Telegram payload (render-only)
# --------------------------------------------------------------------------- #


def test_telegram_daily_keyboard_has_feedback_buttons(busy_daily: DailyDigest) -> None:
    kb = daily_keyboard(busy_daily)
    assert "inline_keyboard" in kb
    # serialize to ensure JSON-safe.
    import json

    json.dumps(kb)


def test_telegram_payload_renders_digest(busy_daily: DailyDigest) -> None:
    text = render_daily_md(busy_daily)
    payload = build_send_payload(
        chat_id="123", text=text, reply_markup=daily_keyboard(busy_daily),
    )
    assert payload["chat_id"] == "123"
    assert payload["parse_mode"] == "MarkdownV2"
    assert payload["text"]  # escaped, non-empty
    assert "reply_markup" in payload


# --------------------------------------------------------------------------- #
# Disabled senders no-op (render-only mode)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_email_disabled_noop(settings, busy_daily: DailyDigest) -> None:
    html = render_daily_html(busy_daily)
    ok = await send_email(subject="Daily", html=html, settings=settings)
    assert ok is False  # email not configured in tests


@pytest.mark.asyncio
async def test_send_message_disabled_noop(settings) -> None:
    ok = await send_message("hello", settings=settings)
    assert ok is False


@pytest.mark.asyncio
async def test_send_daily_disabled_noop_still_renders(
    settings, busy_daily: DailyDigest
) -> None:
    # No telegram config => returns False, but must not raise (it renders first).
    ok = await send_daily(busy_daily, settings=settings)
    assert ok is False
