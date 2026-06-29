"""Unit tests for ingest shared helpers (_util, _feed)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from aidigest.ingest._feed import (
    entry_author,
    entry_dt,
    entry_text,
)
from aidigest.ingest._util import after, html_to_text, is_ai_relevant, parse_dt


def test_is_ai_relevant() -> None:
    assert is_ai_relevant("New LLM beats GPT-4 on reasoning")
    assert is_ai_relevant(None, "a paper about reinforcement learning")
    assert not is_ai_relevant("My cat is cute", "gardening tips")
    assert not is_ai_relevant("", None)


def test_html_to_text_strips_and_truncates() -> None:
    html = "<p>Hello <b>world</b> &amp; friends</p>\n\n<div>more</div>"
    out = html_to_text(html)
    assert "Hello world & friends" in out
    assert "<" not in out
    assert html_to_text(None) == ""
    assert len(html_to_text("x" * 5000, max_len=100)) == 100


def test_parse_dt_variants() -> None:
    iso = parse_dt("2026-06-21T12:00:00Z")
    assert iso is not None and iso.tzinfo is not None and iso.year == 2026
    epoch = parse_dt(1_750_000_000)
    assert epoch is not None and epoch.tzinfo == UTC
    struct = parse_dt(time.gmtime(1_750_000_000))
    assert struct is not None and struct.tzinfo == UTC
    naive = parse_dt("2026-01-01 00:00:00")
    assert naive is not None and naive.tzinfo == UTC  # naive -> UTC attached
    assert parse_dt(None) is None
    assert parse_dt("not a date at all !!!") is None


def test_after_handles_naive_since() -> None:
    dt = datetime(2026, 6, 21, tzinfo=UTC)
    naive_since = datetime(2026, 6, 20)  # naive
    assert after(dt, naive_since) is True
    assert after(None, naive_since) is False
    assert after(datetime(2026, 6, 19, tzinfo=UTC), naive_since) is False


def test_feed_entry_helpers() -> None:
    entry = {
        "title": "A paper",
        "link": "https://example.org/p",
        "published_parsed": time.gmtime(1_750_000_000),
        "summary": "<p>An <b>abstract</b></p>",
        "authors": [{"name": "Ada L."}],
    }
    assert entry_dt(entry) is not None
    assert "abstract" in entry_text(entry)
    assert entry_author(entry) == "Ada L."

    # content[] takes precedence over summary
    entry2 = {"content": [{"value": "<p>body text</p>"}], "summary": "ignored"}
    assert "body text" in entry_text(entry2)
    assert entry_dt({}) is None
    assert entry_author({}) is None
