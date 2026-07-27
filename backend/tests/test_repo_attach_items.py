"""Tests for Repo._attach_items — a single batched query replacing the N+1
per-story `SELECT item_id FROM story_items WHERE story_id = %s` loop (see
`aidigest/db/repo.py`). These exercise `_attach_items` directly by stubbing
`Repo._fetch_dicts`, so they need no real database.
"""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

from datetime import UTC, datetime
from typing import Any

from aidigest.db.repo import Repo
from aidigest.models import Family

NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _story_row(story_id: str) -> dict[str, Any]:
    return {
        "id": story_id,
        "title": f"title-{story_id}",
        "family": Family.ACADEMIA.value,
        "created_at": NOW,
    }


class _CountingFetch:
    """Stand-in for Repo._fetch_dicts: counts calls and serves membership rows
    for whatever story ids are passed to the `story_id = ANY(%s)` query."""

    def __init__(self, membership: dict[str, list[str]]) -> None:
        self.membership = membership
        self.calls = 0

    async def __call__(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        self.calls += 1
        assert "story_items" in sql
        assert "ANY" in sql
        (story_ids,) = params
        rows = [
            {"story_id": sid, "item_id": item_id}
            for sid in story_ids
            for item_id in self.membership.get(sid, [])
        ]
        return rows


async def test_attach_items_groups_interleaved_members_and_preserves_row_order(
    monkeypatch,
) -> None:
    repo = Repo(dsn="postgresql://u:p@h/db")
    fetch = _CountingFetch({"s1": ["i1", "i2"], "s2": ["i3"], "s3": ["i2", "i4"]})
    monkeypatch.setattr(repo, "_fetch_dicts", fetch)

    rows = [_story_row("s2"), _story_row("s3"), _story_row("s1")]
    stories = await repo._attach_items(rows)

    # Row order (final_rank DESC from the caller) is preserved, not query order.
    assert [s.id for s in stories] == ["s2", "s3", "s1"]
    by_id = {s.id: s for s in stories}
    assert by_id["s1"].item_ids == ["i1", "i2"]
    assert by_id["s2"].item_ids == ["i3"]
    assert by_id["s3"].item_ids == ["i2", "i4"]
    assert fetch.calls == 1


async def test_attach_items_keeps_story_with_no_members() -> None:
    repo = Repo(dsn="postgresql://u:p@h/db")

    async def _fetch_dicts(sql: str, params: list[Any]) -> list[dict[str, Any]]:
        return []  # no story_items rows for anyone

    repo._fetch_dicts = _fetch_dicts  # type: ignore[method-assign]

    rows = [_story_row("lonely")]
    stories = await repo._attach_items(rows)

    assert len(stories) == 1
    assert stories[0].id == "lonely"
    assert stories[0].item_ids == []


async def test_attach_items_empty_input_issues_no_query(monkeypatch) -> None:
    repo = Repo(dsn="postgresql://u:p@h/db")
    fetch = _CountingFetch({})
    monkeypatch.setattr(repo, "_fetch_dicts", fetch)

    stories = await repo._attach_items([])

    assert stories == []
    assert fetch.calls == 0


async def test_attach_items_issues_exactly_one_query_for_many_stories(monkeypatch) -> None:
    """The whole point of the fix: N stories must not cost N queries."""
    membership = {f"s{i}": [f"item-{i}-a", f"item-{i}-b"] for i in range(50)}
    repo = Repo(dsn="postgresql://u:p@h/db")
    fetch = _CountingFetch(membership)
    monkeypatch.setattr(repo, "_fetch_dicts", fetch)

    rows = [_story_row(sid) for sid in membership]
    stories = await repo._attach_items(rows)

    assert len(stories) == 50
    assert fetch.calls == 1
    assert all(len(s.item_ids) == 2 for s in stories)
