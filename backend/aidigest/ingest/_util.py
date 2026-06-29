"""Shared parsing helpers for ingestion adapters.

Small, dependency-light utilities every adapter reuses: timezone-aware date
parsing, lightweight HTML-to-text, and a relevance keyword gate for the noisy
community sources. Kept separate so each adapter file stays tiny and focused.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime

from dateutil import parser as date_parser

log = logging.getLogger("aidigest.ingest")

# Keywords that mark a community/front-page story as AI-relevant. Used to filter
# generic HN / Reddit noise down to the topics this engine cares about.
AI_KEYWORDS: tuple[str, ...] = (
    "ai",
    "llm",
    "language model",
    "gpt",
    "claude",
    "gemini",
    "deepseek",
    "qwen",
    "mistral",
    "llama",
    "transformer",
    "neural",
    "machine learning",
    "deep learning",
    "rlhf",
    "rl ",
    "reinforcement learning",
    "diffusion",
    "embedding",
    "fine-tun",
    "agent",
    "openai",
    "anthropic",
    "deepmind",
    "hugging face",
    "huggingface",
    "pytorch",
    "tensor",
    "inference",
    "quantization",
    "moe",
    "mixture of experts",
    "rag",
    "multimodal",
    "vision-language",
    "nlp",
    "arxiv",
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9.+\-]*")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def is_ai_relevant(*texts: str | None) -> bool:
    """True if any provided text mentions an AI-relevant keyword (case-insensitive)."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return False
    return any(kw in blob for kw in AI_KEYWORDS)


def html_to_text(html: str | None, *, max_len: int = 4000) -> str:
    """Crude HTML -> plain text. Strips tags, collapses whitespace, truncates.

    Good enough for feed summaries / blog leads; we never need perfect fidelity.
    """
    if not html:
        return ""
    text = _TAG.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = _WS.sub(" ", text).strip()
    return text[:max_len]


def parse_dt(value: object) -> datetime | None:
    """Parse a variety of date inputs into a timezone-aware UTC datetime.

    Accepts ISO strings, RFC822 strings, epoch seconds (int/float), or a
    feedparser time.struct_time. Returns None when unparseable (caller skips).
    """
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            return _ensure_utc(value)
        if isinstance(value, time.struct_time):
            return datetime.fromtimestamp(time.mktime(value), tz=UTC)
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), tz=UTC)
        if isinstance(value, str) and value.strip():
            return _ensure_utc(date_parser.parse(value))
    except (ValueError, OverflowError, TypeError):
        return None
    return None


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC to naive datetimes; convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def after(dt: datetime | None, since: datetime) -> bool:
    """True when `dt` is known and at-or-after `since` (both made tz-aware)."""
    if dt is None:
        return False
    floor = since if since.tzinfo else since.replace(tzinfo=UTC)
    return dt >= floor


__all__ = [
    "AI_KEYWORDS",
    "after",
    "html_to_text",
    "is_ai_relevant",
    "log",
    "parse_dt",
]
