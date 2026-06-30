"""Pipeline orchestration tests (MOCK LLM, in-memory repo fake, zero network).

Covers gate item (c): `run_ingest`→`run_process`→`run_daily`/`run_weekly`
produce Pydantic-valid digests with no outbound HTTP, and ids are idempotent.
The real psycopg Repo is replaced by an in-memory fake; `ingest_all` is patched
to return a fixed item set (no adapters fire, no network).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import aidigest.flows.pipeline as pipeline
from aidigest.models import (
    DailyDigest,
    DigestKind,
    Family,
    Feedback,
    Item,
    Story,
    WeeklyDigest,
)

# Recent (relative to real now) so items pass the family-aware freshness window in
# run_process; the FakeRepo ignores `since`, so date realism lives on the items.
NOW = datetime.now(UTC) - timedelta(hours=1)


def _items() -> list[Item]:
    return [
        Item.create(
            source="arxiv",
            family=Family.ACADEMIA,
            title="Efficient long-context attention for scalable NLP",
            url="https://arxiv.org/abs/2606.00001",
            raw_text="A linear attention variant matching softmax at 1M context. NeurIPS.",
            published_at=NOW,
            metrics={"citations": 9},
        ),
        Item.create(
            source="hn",
            family=Family.COMMUNITY,
            title="DeepSeek V4 released with new RL recipe",
            url="https://news.ycombinator.com/item?id=1",
            raw_text="DeepSeek V4 open weights, multi-agent self-play, RL post-training.",
            published_at=NOW,
            metrics={"upvotes": 900, "comments": 350},
        ),
    ]


class FakeRepo:
    """In-memory stand-in covering the full surface the pipeline touches."""

    def __init__(self) -> None:
        self.items: dict[str, Item] = {}
        self.stories: dict[str, Story] = {}
        self.dailies: dict[str, DailyDigest] = {}
        self.weeklies: dict[str, WeeklyDigest] = {}
        self.feedback: list[Feedback] = []
        self.evals: list[dict] = []
        self.state: dict[str, dict] = {}

    async def init_schema(self) -> None:  # pragma: no cover - unused here
        return None

    async def upsert_items(self, items: list[Item]) -> int:
        for it in items:
            self.items[it.id] = it
        return len(items)

    async def get_items_without_embedding(self, limit: int = 500) -> list[Item]:
        return [it for it in self.items.values() if it.embedding is None][:limit]

    async def set_item_embedding(self, item_id: str, embedding: list[float]) -> None:
        self.items[item_id] = self.items[item_id].with_embedding(embedding)

    async def get_items_since(self, since: datetime, *, family: Family | None = None) -> list[Item]:
        return list(self.items.values())

    async def get_items_by_ids(self, ids: list[str]) -> list[Item]:
        return [self.items[i] for i in ids if i in self.items]

    async def upsert_stories(self, stories: list[Story]) -> int:
        for st in stories:
            self.stories[st.id] = st
        return len(stories)

    async def get_stories_for_date(self, date: str) -> list[Story]:
        return list(self.stories.values())

    async def delete_stories_for_date(self, date: str) -> int:
        n = len(self.stories)
        self.stories.clear()
        return n

    async def get_feedback(
        self, *, signal: object | None = None, since: datetime | None = None
    ) -> list[Feedback]:
        return list(self.feedback)

    async def save_daily(self, digest: DailyDigest) -> None:
        self.dailies[digest.id] = digest

    async def save_weekly(self, digest: WeeklyDigest) -> None:
        self.weeklies[digest.id] = digest

    async def get_digest(self, digest_id: str) -> DailyDigest | WeeklyDigest | None:
        return self.dailies.get(digest_id) or self.weeklies.get(digest_id)

    async def list_digests(self, *, kind: object | None = None, limit: int = 30) -> list[dict]:
        rows = [
            {"id": d.id, "kind": "daily", "date": d.date, "tier": d.overall_tier.value,
             "quiet": d.quiet_day, "title": d.tldr, "created_at": d.created_at.isoformat()}
            for d in self.dailies.values()
        ]
        return rows[:limit]

    async def save_eval_run(
        self, *, digest_id: str, judge_model: str, scores: dict, notes: str | None = None
    ) -> None:
        self.evals.append({"digest_id": digest_id, "scores": scores, "notes": notes})

    async def save_app_state(self, key: str, value: dict) -> None:
        self.state[key] = dict(value)

    async def get_app_state(self, key: str) -> dict | None:
        return self.state.get(key)

    async def save_interest_vector(self, vector: list[float]) -> None:
        self.state["interest_vector"] = {"vector": list(vector), "dim": len(vector)}

    async def get_interest_vector(self) -> list[float] | None:
        st = self.state.get("interest_vector")
        return list(st["vector"]) if st and "vector" in st else None

    async def save_profile_override(self, profile: dict) -> None:
        self.state["profile_override"] = dict(profile)

    async def get_profile_override(self) -> dict | None:
        return self.state.get("profile_override")


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    """Patch the pipeline's repo + ingestion so it runs fully offline."""
    repo = FakeRepo()

    async def _get_repo() -> FakeRepo:
        return repo

    async def _ingest_all(since: datetime, *, adapters: object = None) -> list[Item]:
        return _items()

    monkeypatch.setattr(pipeline, "get_repo", _get_repo)
    monkeypatch.setattr("aidigest.ingest.registry.ingest_all", _ingest_all)
    return repo


def test_balanced_pool_caps_per_source() -> None:
    # A high-volume source must not crowd out others; each source is capped to N
    # most-recent, bounding the O(n^3) clustering pool.
    from datetime import timedelta

    base = datetime.now(UTC)
    items = [
        Item.create(source="arxiv", family=Family.ACADEMIA, title=f"p{i}",
                    url=f"https://arxiv.org/abs/{i}", published_at=base - timedelta(minutes=i))
        for i in range(50)
    ] + [
        Item.create(source="hn", family=Family.COMMUNITY, title="hot",
                    url="https://news.ycombinator.com/item?id=1", published_at=base),
    ]
    pooled = pipeline.balanced_pool(items, per_source=20)
    assert sum(it.source == "arxiv" for it in pooled) == 20  # capped
    assert sum(it.source == "hn" for it in pooled) == 1  # under cap -> kept
    # the kept arxiv items are the most RECENT (smallest minute offset)
    kept_titles = {it.title for it in pooled if it.source == "arxiv"}
    assert "p0" in kept_titles and "p49" not in kept_titles


@pytest.mark.asyncio
async def test_run_ingest_writes_items(wired: FakeRepo) -> None:
    n = await pipeline.run_ingest()
    assert n == 2
    assert len(wired.items) == 2


@pytest.mark.asyncio
async def test_run_process_builds_stories(wired: FakeRepo) -> None:
    await pipeline.run_ingest()
    count = await pipeline.run_process()
    assert count >= 1
    assert wired.stories
    # all items got embeddings during processing
    assert all(it.embedding is not None for it in wired.items.values())


@pytest.mark.asyncio
async def test_run_daily_produces_valid_digest(wired: FakeRepo) -> None:
    await pipeline.run_ingest()
    digest = await pipeline.run_daily(date="2026-06-21")
    assert isinstance(digest, DailyDigest)
    DailyDigest.model_validate(digest.model_dump(mode="json"))
    assert digest.id == "daily-2026-06-21"
    assert digest.kind == DigestKind.DAILY
    assert digest.tldr.strip()
    assert digest.id in wired.dailies


@pytest.mark.asyncio
async def test_run_daily_is_idempotent(wired: FakeRepo) -> None:
    await pipeline.run_ingest()
    d1 = await pipeline.run_daily(date="2026-06-21")
    d2 = await pipeline.run_daily(date="2026-06-21")
    assert d1.id == d2.id


@pytest.mark.asyncio
async def test_run_weekly_produces_valid_digest(wired: FakeRepo) -> None:
    await pipeline.run_ingest()
    await pipeline.run_process()
    digest = await pipeline.run_weekly(week_of="2026-06-21")
    assert isinstance(digest, WeeklyDigest)
    WeeklyDigest.model_validate(digest.model_dump(mode="json"))
    assert digest.id.startswith("weekly-2026-W")
    assert digest.id in wired.weeklies


@pytest.mark.asyncio
async def test_run_nightly_grades_latest_daily(wired: FakeRepo) -> None:
    await pipeline.run_ingest()
    await pipeline.run_daily(date="2026-06-21")
    await pipeline.run_nightly()
    assert wired.evals  # an eval run was saved for the latest daily


def test_week_of_iso_is_monday() -> None:
    # 2026-06-21 is a Sunday -> week starts Monday 2026-06-15
    assert pipeline._week_of_iso("2026-06-21") == "2026-06-15"


def test_weekly_id_format() -> None:
    assert pipeline._weekly_id("2026-06-15") == "weekly-2026-W25"
