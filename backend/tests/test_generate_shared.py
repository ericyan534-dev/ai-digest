"""Tests for generate._shared helpers and weekly parsing edge cases."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.generate._shared import (
    parse_json_obj,
    sources_block,
    story_items,
    story_links,
    subfields_str,
    venues_str,
)
from aidigest.generate.prompts import load_prompt
from aidigest.generate.weekly import _coerce_family, _iso_week_id, _parse_entries
from aidigest.models import Family, Item, Story

_NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _item(iid: str, body: str = "body") -> Item:
    return Item.create(
        source="hn",
        family=Family.COMMUNITY,
        title=f"t-{iid}",
        url=f"https://e.com/{iid}",
        raw_text=body,
        published_at=_NOW,
    )


# --------------------------------------------------------------------------- #
# parse_json_obj
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ("```json\n{\"a\": 2}\n```", {"a": 2}),
        ("prefix {\"a\": 3} suffix", {"a": 3}),
        ("not json at all", {}),
        ("[1, 2, 3]", {}),  # arrays aren't objects
        ("", {}),
    ],
)
def test_parse_json_obj(raw: str, expected: dict) -> None:
    assert parse_json_obj(raw) == expected


# --------------------------------------------------------------------------- #
# story_items / sources_block / story_links
# --------------------------------------------------------------------------- #


def test_story_items_representative_first() -> None:
    a, b = _item("a"), _item("b")
    story = Story(
        id="s",
        title="s",
        family=Family.COMMUNITY,
        item_ids=[a.id, b.id],
        representative_item_id=b.id,
        created_at=_NOW,
    )
    items = story_items(story, {a.id: a, b.id: b})
    assert items[0].id == b.id  # representative first
    assert {i.id for i in items} == {a.id, b.id}


def test_sources_block_handles_missing_items() -> None:
    story = Story(id="s", title="lonely", family=Family.COMMUNITY, item_ids=["x"], created_at=_NOW)
    block = sources_block(story, {})
    assert "no source text available" in block
    assert "lonely" in block


def test_sources_block_truncates_long_body() -> None:
    long = _item("z", body="x" * 2000)
    story = Story(
        id="s",
        title="s",
        family=Family.COMMUNITY,
        item_ids=[long.id],
        representative_item_id=long.id,
        created_at=_NOW,
    )
    block = sources_block(story, {long.id: long})
    assert "…" in block


def test_story_links_dedupes() -> None:
    a = _item("a")
    story = Story(
        id="s",
        title="s",
        family=Family.COMMUNITY,
        item_ids=[a.id, a.id],
        representative_item_id=a.id,
        created_at=_NOW,
    )
    assert story_links(story, {a.id: a}) == [a.url]


def test_subfields_and_venues_str() -> None:
    profile = {"subfields": ["A", "B"], "venues": ["NeurIPS"]}
    assert subfields_str(profile) == "A; B"
    assert venues_str(profile) == "NeurIPS"
    assert subfields_str({}) == ""
    assert venues_str({}) == ""


# --------------------------------------------------------------------------- #
# weekly internals
# --------------------------------------------------------------------------- #


def test_iso_week_id_valid_and_fallback() -> None:
    assert _iso_week_id("2026-06-15").startswith("weekly-2026-W")
    assert _iso_week_id("garbage") == "weekly-garbage"


def test_coerce_family_defaults_to_meta() -> None:
    assert _coerce_family("academia") == Family.ACADEMIA
    assert _coerce_family("bogus") == Family.META
    assert _coerce_family(None) == Family.META


def test_parse_entries_skips_bad_rows() -> None:
    rows = [
        {"title": "Good", "url": "https://x", "one_liner": "ol", "family": "academia"},
        {"title": "", "family": "industry"},  # empty title -> skipped
        "not a dict",  # skipped
        {"title": "NoUrl", "family": "community"},  # url optional
    ]
    entries = _parse_entries(rows)
    titles = [e.title for e in entries]
    assert titles == ["Good", "NoUrl"]
    assert entries[0].family == Family.ACADEMIA
    assert entries[1].url is None
    assert _parse_entries("not a list") == []


def test_load_prompt_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does-not-exist")
