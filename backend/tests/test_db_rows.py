"""Tests for db/_rows.py — pure row<->model mapping (no DB handle needed)."""

from __future__ import annotations

from datetime import UTC, datetime

from aidigest.db import _rows
from aidigest.models import (
    DailyDigest,
    DigestKind,
    Family,
    FeedbackSignal,
    FeedbackTargetKind,
    ImportanceTier,
    Item,
    Source,
    Story,
    WeeklyDigest,
)

NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def test_item_round_trip() -> None:
    item = Item.create(
        source="hn", family=Family.COMMUNITY, title="X", url="https://e/1",
        raw_text="body", published_at=NOW, metrics={"upvotes": 5},
    ).with_embedding([0.1] * 4)
    params = _rows.item_to_params(item)
    row = {**params, "fetched_at": item.fetched_at}
    back = _rows.row_to_item(row)
    assert back.id == item.id
    assert back.title == "X"
    assert back.embedding == [0.1] * 4
    assert back.metrics == {"upvotes": 5}


def test_item_row_without_embedding() -> None:
    row = {
        "id": "i", "source": "hn", "family": "community", "url": None, "title": "t",
        "author": None, "published_at": NOW, "fetched_at": NOW, "raw_text": "",
        "embedding": None, "metrics": {}, "raw": {},
    }
    assert _rows.row_to_item(row).embedding is None


def test_story_round_trip() -> None:
    story = Story(
        id="s-1", title="T", family=Family.ACADEMIA, item_ids=["a", "b"],
        representative_item_id="a", embedding=[0.2] * 3, importance=0.7,
        personal=0.6, final_rank=0.65, tier=ImportanceTier.NOTABLE,
        mention_count=2, created_at=NOW,
    )
    params = _rows.story_to_params(story)
    back = _rows.row_to_story(params, ["a", "b"])
    assert back.id == "s-1"
    assert back.tier == ImportanceTier.NOTABLE
    assert back.item_ids == ["a", "b"]
    assert back.mention_count == 2


def test_feedback_row() -> None:
    row = {
        "id": 7, "user": "me", "target_id": "story-1",
        "target_kind": "story", "signal": "up", "value": 1.0,
        "text": None, "created_at": NOW,
    }
    fb = _rows.row_to_feedback(row)
    assert fb.id == "7"
    assert fb.target_kind == FeedbackTargetKind.STORY
    assert fb.signal == FeedbackSignal.UP


def test_source_row_defaults() -> None:
    row = {"name": "arxiv", "family": "academia"}
    src = _rows.row_to_source(row)
    assert isinstance(src, Source)
    assert src.authority == 0.5
    assert src.enabled is True


def test_digest_summary_row_daily_uses_tldr() -> None:
    digest = DailyDigest(
        id="daily-2026-06-21", date="2026-06-21",
        tldr="Quiet day — nothing major shipped.",
        overall_tier=ImportanceTier.QUIET_DAY, quiet_day=True,
    )
    row = {
        "id": digest.id, "kind": "daily", "date": "2026-06-21",
        "tier": "quiet_day", "quiet": True,
        "content": digest.model_dump(mode="json"), "created_at": NOW,
    }
    summary = _rows.digest_summary_row(row)
    assert summary["title"] == "Quiet day — nothing major shipped."
    assert summary["quiet"] is True
    assert summary["created_at"] == NOW.isoformat()


def test_digest_summary_row_weekly_uses_title() -> None:
    row = {
        "id": "weekly-2026-W25", "kind": "weekly", "date": "2026-06-15",
        "tier": "notable", "quiet": False,
        "content": {"title": "The Week in Agents"}, "created_at": NOW,
    }
    assert _rows.digest_summary_row(row)["title"] == "The Week in Agents"


def test_deserialize_digest_dispatches_on_kind() -> None:
    daily = DailyDigest(
        id="daily-x", date="2026-06-21", tldr="t",
        overall_tier=ImportanceTier.MINOR,
    )
    weekly = WeeklyDigest(
        id="weekly-x", week_of="2026-06-15", title="T", lede="l",
        body_markdown="b", overall_tier=ImportanceTier.MINOR,
    )
    d = _rows.deserialize_digest(daily.model_dump(mode="json"), DigestKind.DAILY.value)
    w = _rows.deserialize_digest(weekly.model_dump(mode="json"), DigestKind.WEEKLY.value)
    assert isinstance(d, DailyDigest)
    assert isinstance(w, WeeklyDigest)


def test_iso_handles_string_and_none() -> None:
    assert _rows._iso("2026-06-21") == "2026-06-21"
    assert _rows._iso(None) == ""
