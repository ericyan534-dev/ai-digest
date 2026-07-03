"""Grounding guards — thin-source detection + number-grounding (anti-fabrication)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

from aidigest.generate._grounding import (  # noqa: E402
    is_thin_source,
    strip_ungrounded,
    substantive_source_len,
    ungrounded_numbers,
)
from aidigest.models import Family, Item, Story  # noqa: E402


def _item(iid: str, title: str, body: str) -> Item:
    return Item.create(
        source="hn", family=Family.COMMUNITY, title=title, url=f"https://e/{iid}",
        raw_text=body, published_at=datetime.now(UTC),
    ).model_copy(update={"id": iid})


def _story(item: Item) -> Story:
    return Story(id="s", title=item.title, family=Family.COMMUNITY,
                 item_ids=[item.id], representative_item_id=item.id)


# --------------------------------------------------------------------------- #
# Thin-source detection
# --------------------------------------------------------------------------- #


def test_title_only_source_is_thin() -> None:
    it = _item("i1", "GPT-6 released", body="")  # HN title-only
    by_id = {it.id: it}
    story = _story(it)
    assert substantive_source_len(story, by_id) == 0
    assert is_thin_source(story, by_id) is True


def test_substantive_source_is_not_thin() -> None:
    it = _item("i2", "New model", body="x" * 400)  # real article body
    by_id = {it.id: it}
    assert is_thin_source(_story(it), by_id) is False


# --------------------------------------------------------------------------- #
# Number grounding
# --------------------------------------------------------------------------- #


def test_ungrounded_numbers_flags_invented_benchmark() -> None:
    source = "The model improves reasoning on the benchmark suite."
    text = "It delivers a 40% speedup and a 2.6x throughput gain."
    flagged = ungrounded_numbers(text, source)
    assert "40" in flagged and "2.6" in flagged


def test_grounded_numbers_survive() -> None:
    source = "Reaches 92.3 on MMLU, a 40% cut in latency across 1.6T params."
    text = "Hits 92.3 on MMLU with a 40% latency cut."
    assert ungrounded_numbers(text, source) == []


def test_strip_ungrounded_drops_fabricated_sentence() -> None:
    source = "Anthropic and California sign a deal for Claude access."
    text = "Anthropic signed a California deal. It cuts costs 50% and adds 3x capacity."
    cleaned, dropped = strip_ungrounded(text, source)
    assert dropped == 1
    assert "Anthropic signed a California deal." in cleaned
    assert "50%" not in cleaned and "3x" not in cleaned


def test_strip_tolerates_reworded_units() -> None:
    # "$50M" in text vs "$50 million" in source -> core "50" is present -> kept.
    source = "Patronus AI raised $50 million to build testing worlds."
    text = "Patronus raised $50M for agent testing."
    cleaned, dropped = strip_ungrounded(text, source)
    assert dropped == 0 and "50" in cleaned
