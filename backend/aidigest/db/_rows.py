"""Row <-> domain-model mapping helpers for the repository.

Kept separate from `repo.py` so the repo file stays focused on I/O. These are
pure functions (no DB handles) and have no psycopg dependency, so they are
trivially unit-testable offline.
"""

from __future__ import annotations

from typing import Any

from aidigest.models import (
    DailyDigest,
    DigestKind,
    Family,
    Feedback,
    FeedbackSignal,
    FeedbackTargetKind,
    ImportanceTier,
    Item,
    Source,
    Story,
    WeeklyDigest,
)


def item_to_params(item: Item) -> dict[str, Any]:
    """Flatten an Item into upsert parameters (embedding handled separately)."""
    return {
        "id": item.id,
        "source": item.source,
        "family": item.family.value,
        "url": item.url,
        "title": item.title,
        "author": item.author,
        "published_at": item.published_at,
        "fetched_at": item.fetched_at,
        "raw_text": item.raw_text,
        "embedding": item.embedding,
        "metrics": item.metrics,
        "raw": item.raw,
    }


def row_to_item(row: dict[str, Any]) -> Item:
    """Build an Item from a DB row dict."""
    embedding = row.get("embedding")
    return Item(
        id=row["id"],
        source=row["source"],
        family=Family(row["family"]),
        url=row.get("url"),
        title=row["title"],
        author=row.get("author"),
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        raw_text=row.get("raw_text") or "",
        embedding=list(embedding) if embedding is not None else None,
        metrics=row.get("metrics") or {},
        raw=row.get("raw") or {},
    )


def story_to_params(story: Story) -> dict[str, Any]:
    return {
        "id": story.id,
        "title": story.title,
        "family": story.family.value,
        "representative_item_id": story.representative_item_id,
        "embedding": story.embedding,
        "importance": story.importance,
        "personal": story.personal,
        "final_rank": story.final_rank,
        "tier": story.tier.value,
        "mention_count": story.mention_count,
        "created_at": story.created_at,
    }


def row_to_story(row: dict[str, Any], item_ids: list[str]) -> Story:
    embedding = row.get("embedding")
    return Story(
        id=row["id"],
        title=row["title"],
        family=Family(row["family"]),
        item_ids=item_ids,
        representative_item_id=row.get("representative_item_id"),
        embedding=list(embedding) if embedding is not None else None,
        importance=row.get("importance") or 0.0,
        personal=row.get("personal") or 0.0,
        final_rank=row.get("final_rank") or 0.0,
        tier=ImportanceTier(row.get("tier") or "minor"),
        mention_count=row.get("mention_count") or 1,
        created_at=row["created_at"],
    )


def row_to_feedback(row: dict[str, Any]) -> Feedback:
    return Feedback(
        id=str(row["id"]) if row.get("id") is not None else None,
        user=row.get("user") or "me",
        target_id=row["target_id"],
        target_kind=FeedbackTargetKind(row["target_kind"]),
        signal=FeedbackSignal(row["signal"]),
        value=row.get("value") or 1.0,
        text=row.get("text"),
        created_at=row["created_at"],
    )


def row_to_source(row: dict[str, Any]) -> Source:
    return Source(
        name=row["name"],
        family=Family(row["family"]),
        url=row.get("url"),
        authority=row.get("authority") if row.get("authority") is not None else 0.5,
        enabled=bool(row.get("enabled", True)),
        config=row.get("config") or {},
    )


def digest_summary_row(row: dict[str, Any]) -> dict[str, Any]:
    """Lightweight archive row: {id, kind, date, tier, quiet, title, created_at}."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "date": _iso(row.get("date")),
        "tier": row.get("tier"),
        "quiet": bool(row.get("quiet", False)),
        "title": _digest_title(row),
        "created_at": _iso(row.get("created_at")),
    }


def _iso(value: Any) -> str:
    """Render a date/datetime (or already-string) as an ISO string."""
    if value is None:
        return ""
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _digest_title(row: dict[str, Any]) -> str:
    """title = daily tldr / weekly title, pulled from the stored content jsonb."""
    content = row.get("content") or {}
    if row.get("kind") == DigestKind.WEEKLY.value:
        return str(content.get("title", ""))
    return str(content.get("tldr", ""))


def deserialize_digest(content: dict[str, Any], kind: str) -> DailyDigest | WeeklyDigest:
    if kind == DigestKind.WEEKLY.value:
        return WeeklyDigest.model_validate(content)
    return DailyDigest.model_validate(content)


__all__ = [
    "item_to_params",
    "row_to_item",
    "story_to_params",
    "row_to_story",
    "row_to_feedback",
    "row_to_source",
    "digest_summary_row",
    "deserialize_digest",
]
