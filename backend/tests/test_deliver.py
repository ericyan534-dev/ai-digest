"""Tests for aidigest.deliver — renderers + senders, all offline (no network).

Senders are exercised in their render-only / no-op path (no token/key set), and
the pure payload builders are asserted directly so we cover the wire shape
without touching the network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.config import Settings
from aidigest.deliver import email_resend, render_md, telegram_bot
from aidigest.deliver.style import ACCENT, MONO, PAPER, SERIF, feedback_url
from aidigest.models import (
    DailyDigest,
    DigestSection,
    Family,
    ImportanceTier,
    StorySummary,
    WeeklyDigest,
    WeeklyShortlistEntry,
)

NOW = datetime(2026, 6, 21, 14, 3, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _breakthrough_summary() -> StorySummary:
    return StorySummary(
        story_id="deepseek-v4-ab12cd",
        title="DeepSeek V4 released",
        family=Family.ACADEMIA,
        tier=ImportanceTier.BREAKTHROUGH,
        takeaway=(
            "DeepSeek V4 ships a new RL post-training recipe. It reports large "
            "gains on reasoning benchmarks while halving inference cost. The "
            "architecture leans on a sparse MoE with a refreshed router."
        ),
        why_it_matters="Directly relevant to RL for NLP & Efficient NLP subfields.",
        links=["https://arxiv.org/abs/2606.00001", "https://example.com/blog"],
        tags=["LLMs", "Optimization"],
        score=0.91,
    )


def _minor_summary() -> StorySummary:
    return StorySummary(
        story_id="lib-bump-ef34gh",
        title="A minor library bump",
        family=Family.COMMUNITY,
        tier=ImportanceTier.MINOR,
        takeaway="Routine version bump.",
        why_it_matters="",
        links=[],
        tags=[],
        score=0.18,
    )


def daily_digest() -> DailyDigest:
    return DailyDigest(
        id="daily-2026-06-21",
        date="2026-06-21",
        tldr="DeepSeek V4 makes reasoning cheap.",
        overall_tier=ImportanceTier.BREAKTHROUGH,
        quiet_day=False,
        sections=[
            DigestSection(
                family=Family.ACADEMIA,
                heading="Academia",  # plain (production); render adds the family emoji
                summaries=[_breakthrough_summary()],
            ),
            DigestSection(
                family=Family.COMMUNITY,
                heading="Community",
                summaries=[_minor_summary()],
            ),
        ],
        story_ids=["deepseek-v4-ab12cd", "lib-bump-ef34gh"],
        model="gemini-3.5-flash",
        created_at=NOW,
    )


def quiet_daily_digest() -> DailyDigest:
    return DailyDigest(
        id="daily-2026-06-22",
        date="2026-06-22",
        tldr="Quiet day — nothing major shipped.",
        overall_tier=ImportanceTier.QUIET_DAY,
        quiet_day=True,
        sections=[],
        story_ids=[],
        created_at=NOW,
    )


def weekly_digest() -> WeeklyDigest:
    return WeeklyDigest(
        id="weekly-2026-W25",
        week_of="2026-06-15",
        title="The week reasoning got cheap",
        lede="A strong narrative opening about cheaper reasoning.",
        body_markdown="# Lead\n\nThe big theme this week was efficiency.\n\n## Threads\n\nMore detail here.",
        overall_tier=ImportanceTier.NOTABLE,
        quiet_week=False,
        shortlist=[
            WeeklyShortlistEntry(
                title="DeepSeek V4 paper",
                url="https://arxiv.org/abs/2606.00001",
                one_liner="The RL recipe everyone is copying.",
                family=Family.ACADEMIA,
            )
        ],
        on_my_radar=[
            WeeklyShortlistEntry(
                title="NeurIPS submission deadline",
                url=None,
                one_liner="Watch for camera-ready drops.",
                family=Family.ACADEMIA,
            )
        ],
        story_ids=["deepseek-v4-ab12cd"],
        candidate_count=3,
        winning_candidate=1,
        model="gemini-3.5-flash",
        judge_model="gemini-3.5-flash",
        created_at=NOW,
    )


def _disabled_settings() -> Settings:
    # No resend/telegram credentials => both channels disabled (render-only).
    return Settings(AIDIGEST_LLM_MOCK=True)  # type: ignore[call-arg]


def _email_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        AIDIGEST_LLM_MOCK=True,
        RESEND_API_KEY="re_test_key",
        DIGEST_FROM_EMAIL="digest@example.com",
        DIGEST_TO_EMAIL="me@example.com",
    )


def _telegram_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        AIDIGEST_LLM_MOCK=True,
        TELEGRAM_BOT_TOKEN="123:abc",
        TELEGRAM_CHAT_ID="999",
    )


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def test_render_daily_md_structure() -> None:
    md = render_md.render_daily_md(daily_digest())
    assert md.startswith("# Daily — 2026-06-21")
    assert "DeepSeek V4 makes reasoning cheap." in md
    assert "## 🎓 Academia" in md
    assert "BREAKTHROUGH" in md
    assert "Why it matters:" in md
    assert "https://arxiv.org/abs/2606.00001" in md
    assert md.endswith("\n")


def test_render_daily_md_quiet_day_is_honest() -> None:
    md = render_md.render_daily_md(quiet_daily_digest())
    assert "Quiet day — nothing major shipped." in md
    assert "QUIET" in md
    # No fabricated sections.
    assert "## " not in md


def test_breakthrough_takeaway_longer_than_minor_in_md() -> None:
    md = render_md.render_daily_md(daily_digest())
    bt = "DeepSeek V4 ships a new RL post-training recipe."
    minor = "Routine version bump."
    assert bt in md and minor in md
    assert len(bt) > len(minor)


def test_render_weekly_md_structure() -> None:
    md = render_md.render_weekly_md(weekly_digest())
    assert md.startswith("# The week reasoning got cheap")
    assert "Week of 2026-06-15" in md
    assert "What I'd actually read this week" in md
    assert "On my radar" in md
    assert "[DeepSeek V4 paper](https://arxiv.org/abs/2606.00001)" in md
    # entry without url renders as plain text, not a broken link
    assert "NeurIPS submission deadline" in md


# --------------------------------------------------------------------------- #
# HTML rendering (Hybrid Editorial tokens)
# --------------------------------------------------------------------------- #


def test_render_daily_html_has_editorial_tokens() -> None:
    html = render_md.render_daily_html(daily_digest())
    assert PAPER in html
    assert SERIF in html
    assert MONO in html
    assert ACCENT in html
    assert "DeepSeek V4 makes reasoning cheap." in html
    assert "BREAKTHROUGH" in html


def test_render_daily_html_inline_feedback_links_point_at_api() -> None:
    html = render_md.render_daily_html(daily_digest(), api_base="https://api.test")
    assert "https://api.test/api/feedback/click" in html
    assert "signal=up" in html
    assert "signal=down" in html
    assert "target_id=deepseek-v4-ab12cd" in html
    assert "👍" in html and "👎" in html


def test_render_daily_html_quiet_day() -> None:
    html = render_md.render_daily_html(quiet_daily_digest())
    assert "Quiet day — nothing major shipped." in html


def test_render_weekly_html_renders_body_and_shortlists() -> None:
    html = render_md.render_weekly_html(weekly_digest())
    assert "The week reasoning got cheap" in html
    assert "What I&#x27;d actually read this week" in html or "What I'd actually read" in html
    assert "On my radar" in html
    assert "https://arxiv.org/abs/2606.00001" in html


def test_feedback_url_builder() -> None:
    url = feedback_url(
        "https://api.test/", target_id="x-1", target_kind="story", signal="up"
    )
    assert url == "https://api.test/api/feedback/click?target_id=x-1&target_kind=story&signal=up&value=1"


def test_html_escapes_untrusted_text() -> None:
    s = StorySummary(
        story_id="x",
        title="<script>alert(1)</script>",
        family=Family.INDUSTRY,
        tier=ImportanceTier.NOTABLE,
        takeaway="ok",
        why_it_matters="",
    )
    section = DigestSection(family=Family.INDUSTRY, heading="Industry", summaries=[s])
    digest = daily_digest().model_copy(update={"sections": [section]})
    html = render_md.render_daily_html(digest)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# Email sender
# --------------------------------------------------------------------------- #


def test_email_build_payload_shape() -> None:
    payload = email_resend.build_payload(
        subject="Daily", html="<p>hi</p>", to="me@example.com", from_email="d@example.com"
    )
    assert payload == {
        "from": "d@example.com",
        "to": ["me@example.com"],
        "subject": "Daily",
        "html": "<p>hi</p>",
    }


@pytest.mark.asyncio
async def test_send_email_noop_when_disabled() -> None:
    # No credentials => render-only; must return False and never raise/network.
    ok = await email_resend.send_email(
        subject="Daily", html="<p>hi</p>", settings=_disabled_settings()
    )
    assert ok is False


def test_email_settings_enabled_flag() -> None:
    assert _email_settings().email_enabled is True
    assert _disabled_settings().email_enabled is False


# --------------------------------------------------------------------------- #
# Telegram sender
# --------------------------------------------------------------------------- #


def test_telegram_feedback_callback_is_compact_and_decodable() -> None:
    cb = telegram_bot.feedback_callback(signal="up", target_id="daily-2026-06-21")
    assert cb == "fb:up:digest:daily-2026-06-21"
    assert len(cb.encode("utf-8")) <= 64


def test_telegram_daily_keyboard_has_up_and_down() -> None:
    kb = telegram_bot.daily_keyboard(daily_digest())
    row = kb["inline_keyboard"][0]
    assert "👍" in row[0]["text"]
    assert "👎" in row[1]["text"]
    assert row[0]["callback_data"].startswith("fb:up:")
    assert row[1]["callback_data"].startswith("fb:down:")


def test_telegram_escape_md() -> None:
    assert telegram_bot.escape_md("a-b.c") == "a\\-b\\.c"
    assert telegram_bot.escape_md("# Daily") == "\\# Daily"


def test_telegram_build_send_payload_shape() -> None:
    kb = telegram_bot.daily_keyboard(daily_digest())
    payload = telegram_bot.build_send_payload(chat_id="999", text="# Hi", reply_markup=kb)
    assert payload["chat_id"] == "999"
    assert payload["parse_mode"] == "MarkdownV2"
    assert payload["disable_web_page_preview"] is True
    assert payload["text"] == "\\# Hi"
    assert payload["reply_markup"] == kb


@pytest.mark.asyncio
async def test_send_message_noop_when_disabled() -> None:
    ok = await telegram_bot.send_message("hi", settings=_disabled_settings())
    assert ok is False


@pytest.mark.asyncio
async def test_send_daily_noop_when_disabled_but_renders() -> None:
    # Even disabled, send_daily must render the digest without raising.
    ok = await telegram_bot.send_daily(daily_digest(), settings=_disabled_settings())
    assert ok is False


def test_telegram_settings_enabled_flag() -> None:
    assert _telegram_settings().telegram_enabled is True
    assert _disabled_settings().telegram_enabled is False


# --------------------------------------------------------------------------- #
# Network send paths (fake httpx client — no real network)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, *, json_body: dict | None = None) -> None:
        self._json = json_body or {}

    def raise_for_status(self) -> None:  # noqa: D401 - mimic httpx
        return None

    def json(self) -> dict:
        return self._json


class _FakeClient:
    """Captures the last POST so tests can assert on the wire payload."""

    last_url: str | None = None
    last_json: dict | None = None
    last_headers: dict | None = None
    response_json: dict | None = None

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict | None = None,
        headers: dict | None = None,
    ) -> _FakeResponse:
        type(self).last_url = url
        type(self).last_json = json
        type(self).last_headers = headers
        return _FakeResponse(json_body=type(self).response_json)


@pytest.mark.asyncio
async def test_send_email_posts_to_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.last_url = None
    monkeypatch.setattr(email_resend, "make_async_client", lambda **kw: _FakeClient())
    ok = await email_resend.send_email(
        subject="Daily", html="<p>hi</p>", settings=_email_settings()
    )
    assert ok is True
    assert _FakeClient.last_url == email_resend.RESEND_ENDPOINT
    assert _FakeClient.last_json == {
        "from": "digest@example.com",
        "to": ["me@example.com"],
        "subject": "Daily",
        "html": "<p>hi</p>",
    }
    assert _FakeClient.last_headers is not None
    assert _FakeClient.last_headers["Authorization"] == "Bearer re_test_key"


@pytest.mark.asyncio
async def test_send_message_posts_to_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.last_url = None
    _FakeClient.response_json = {"ok": True}
    monkeypatch.setattr(telegram_bot, "make_async_client", lambda **kw: _FakeClient())
    ok = await telegram_bot.send_message("hello world", settings=_telegram_settings())
    assert ok is True
    assert _FakeClient.last_url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert _FakeClient.last_json is not None
    assert _FakeClient.last_json["chat_id"] == "999"
    _FakeClient.response_json = None


@pytest.mark.asyncio
async def test_send_daily_posts_with_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.last_url = None
    _FakeClient.response_json = {"ok": True}
    monkeypatch.setattr(telegram_bot, "make_async_client", lambda **kw: _FakeClient())
    ok = await telegram_bot.send_daily(daily_digest(), settings=_telegram_settings())
    assert ok is True
    assert _FakeClient.last_json is not None
    assert "reply_markup" in _FakeClient.last_json
    kb = _FakeClient.last_json["reply_markup"]["inline_keyboard"][0]
    assert kb[0]["callback_data"].startswith("fb:up:")
    _FakeClient.response_json = None
