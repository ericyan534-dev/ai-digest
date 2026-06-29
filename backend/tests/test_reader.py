"""Web-reader fallback (Jina) — gating + thin-body fetch."""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402

import aidigest.ingest._reader as reader  # noqa: E402
from aidigest.config import Settings  # noqa: E402


def _live_settings(min_chars: int = 100) -> Settings:
    return Settings(  # type: ignore[call-arg]
        AIDIGEST_LLM_MOCK=False,
        AIDIGEST_WEB_READER=True,
        AIDIGEST_WEB_READER_MIN_CHARS=min_chars,
    )


@pytest.mark.asyncio
async def test_reader_skips_in_mock_mode() -> None:
    # Global mock mode -> reader never touches the network.
    assert await reader.fetch_readable("http://x", "short") == "short"


@pytest.mark.asyncio
async def test_reader_skips_long_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "get_settings", lambda: _live_settings(min_chars=10))
    body = "this body is already long enough to skip the reader"
    assert await reader.fetch_readable("http://x", body) == body


@pytest.mark.asyncio
async def test_reader_skips_bad_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "get_settings", lambda: _live_settings())
    assert await reader.fetch_readable(None, "thin") == "thin"
    assert await reader.fetch_readable("not-a-url", "thin") == "thin"


@pytest.mark.asyncio
async def test_reader_fetches_when_thin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "get_settings", lambda: _live_settings(min_chars=100))

    async def _fake_fetch_text(url: str, *, client=None) -> str:  # type: ignore[no-untyped-def]
        assert url.startswith("https://r.jina.ai/")
        return "X" * 500

    monkeypatch.setattr(reader, "fetch_text", _fake_fetch_text)
    out = await reader.fetch_readable("http://x", "thin")
    assert len(out) == 500


@pytest.mark.asyncio
async def test_reader_returns_original_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "get_settings", lambda: _live_settings(min_chars=100))

    async def _boom(url: str, *, client=None) -> str:  # type: ignore[no-untyped-def]
        raise RuntimeError("reader down")

    monkeypatch.setattr(reader, "fetch_text", _boom)
    assert await reader.fetch_readable("http://x", "thin") == "thin"
