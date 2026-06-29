"""Web-reader fallback: fetch clean readable text for a URL via Jina Reader.

Lab-blog RSS often carries only a title + a lead sentence, so an INDUSTRY item's
``raw_text`` can be nearly empty — starving dedup/cluster/rank of signal. When a
body is thinner than ``web_reader_min_chars``, we fetch ``https://r.jina.ai/<url>``
(clean Markdown, no API key) and use it instead.

Best-effort and offline-safe: any failure returns the original text unchanged,
and the reader is skipped entirely in mock/offline mode (``AIDIGEST_LLM_MOCK=1``)
so the test suite never touches the network.
"""

from __future__ import annotations

from typing import Any

from aidigest.config import get_settings
from aidigest.ingest._util import log
from aidigest.ingest.base import fetch_text

_JINA_PREFIX = "https://r.jina.ai/"
_MAX_CHARS = 8000


def _needs_reader(text: str, *, min_chars: int) -> bool:
    return len((text or "").strip()) < min_chars


async def fetch_readable(
    url: str | None, current_text: str, *, client: Any | None = None
) -> str:
    """Return a richer body for ``url`` when the current text is too thin.

    Returns ``current_text`` unchanged when the reader is disabled, offline, the
    URL is unusable, the body is already long enough, or the fetch fails.
    """
    settings = get_settings()
    if not settings.web_reader_enabled or settings.llm_mock:
        return current_text
    if not url or not url.startswith(("http://", "https://")):
        return current_text
    if not _needs_reader(current_text, min_chars=settings.web_reader_min_chars):
        return current_text
    try:
        readable = await fetch_text(f"{_JINA_PREFIX}{url}", client=client)
    except Exception as exc:  # noqa: BLE001 — reader is best-effort, never fatal
        log.warning("web reader failed for %s: %s", url, exc)
        return current_text
    readable = (readable or "").strip()
    if len(readable) > len(current_text.strip()):
        return readable[:_MAX_CHARS]
    return current_text


__all__ = ["fetch_readable"]
