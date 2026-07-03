"""Grounding guards — keep generated text tied to REAL source content.

Two deterministic defenses against LLM fabrication in user-facing text:

1. Thin-source detection: when a story's only source is a headline (title-only HN/
   Reddit items, or a bare link), the model has nothing to ground a multi-sentence
   takeaway on and WILL invent numbers/mechanisms. Callers use this to fall back to
   the real title instead of fabricated depth.

2. Number grounding: a benchmark score / % / $ figure / magnitude that appears in
   the generated text but NOWHERE in the source is fabricated. ``strip_ungrounded``
   drops the sentence carrying it (a fake "40% speedup" is worse than a missing one).

Pure functions, no I/O — safe to unit-test and cheap to run on every story.
"""

from __future__ import annotations

import re

from aidigest.generate._shared import story_items
from aidigest.models import Item, Story

# Minimum substantive source body (across a story's items) to allow a written
# takeaway. Below this, only the real title is trustworthy.
_MIN_SOURCE_CHARS = 200

# "Significant" numeric claims worth grounding: money, percentages, multipliers, and
# magnitudes (k/M/B/T + units). Bare small integers ("GPT 4", "2 models") are ignored
# — they are rarely fabricated benchmarks and would cause false positives.
_SIG_NUM_RE = re.compile(
    r"\$\s*\d[\d,]*(?:\.\d+)?\s*(?:k|m|bn?|billion|million|trillion|b|t)?"  # money
    r"|\d[\d,]*(?:\.\d+)?\s*(?:%|×|x\b|k\b|bn?\b|billion|million|trillion|"
    r"tokens?|params?|pts?|bps|ms|gb|tb|flops?|b\b|t\b)"  # number + unit/magnitude
    r"|\d[\d,]*\.\d+",  # any decimal (benchmark scores like 92.3)
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def substantive_source_len(story: Story, items_by_id: dict[str, Item]) -> int:
    """Total length of real body text across a story's member items."""
    return sum(len((it.raw_text or "").strip()) for it in story_items(story, items_by_id))


def is_thin_source(story: Story, items_by_id: dict[str, Item]) -> bool:
    """True when the only reliable content is the title (no body to ground depth on)."""
    return substantive_source_len(story, items_by_id) < _MIN_SOURCE_CHARS


def source_digits(story: Story, items_by_id: dict[str, Item]) -> str:
    """All source text (titles + bodies), comma-stripped, for number membership tests."""
    parts = [story.title]
    for it in story_items(story, items_by_id):
        parts.append(it.title or "")
        parts.append(it.raw_text or "")
    return re.sub(r",", "", " ".join(parts))


def _num_core(token: str) -> str:
    """The bare numeric core of a significant token ('$1.6T' -> '1.6', '40%' -> '40')."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", token)
    return m.group(0).replace(",", "") if m else ""


def ungrounded_numbers(text: str, grounding: str) -> list[str]:
    """Significant number cores present in `text` but absent from the `grounding` text."""
    ground = re.sub(r",", "", grounding)
    out: list[str] = []
    for tok in _SIG_NUM_RE.findall(text or ""):
        core = _num_core(tok)
        if core and core not in ground:
            out.append(core)
    return out


def strip_ungrounded(text: str, grounding: str) -> tuple[str, int]:
    """Drop sentences whose significant numbers are absent from the source.

    Returns (cleaned_text, dropped_sentence_count). A fabricated benchmark is worse
    than a shorter takeaway, so the offending sentence is removed entirely.
    """
    if not text:
        return text, 0
    ground = re.sub(r",", "", grounding)
    kept: list[str] = []
    dropped = 0
    for sent in _SENTENCE_SPLIT_RE.split(text.strip()):
        bad = [c for tok in _SIG_NUM_RE.findall(sent) if (c := _num_core(tok)) and c not in ground]
        if bad:
            dropped += 1
            continue
        kept.append(sent)
    return " ".join(kept).strip(), dropped


__all__ = [
    "substantive_source_len",
    "is_thin_source",
    "source_digits",
    "ungrounded_numbers",
    "strip_ungrounded",
]
