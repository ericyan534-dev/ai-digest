"""Tests for the adapter registry: assembly, lookup, concurrency, isolation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.ingest import registry
from aidigest.ingest.base import Adapter
from aidigest.models import Family, Item


@pytest.fixture
def since() -> datetime:
    return datetime(2026, 6, 1, tzinfo=UTC)


def test_all_adapters_conform_to_protocol() -> None:
    adapters = registry.all_adapters()
    assert len(adapters) >= 14  # 7 core + 7 RSS feeds (verified-live URLs only)
    names = {a.name for a in adapters}
    # core adapters present
    for expected in {
        "hn",
        "reddit",
        "arxiv",
        "openreview",
        "semantic_scholar",
        "hf_papers",
        "smol.ai",
    }:
        assert expected in names
    # RSS feeds present and namespaced
    assert any(n.startswith("rss:") for n in names)
    for a in adapters:
        assert isinstance(a, Adapter)
        assert isinstance(a.family, Family)


def test_get_adapter_lookup() -> None:
    assert registry.get_adapter("hn") is not None
    assert registry.get_adapter("rss:openai") is not None
    assert registry.get_adapter("does-not-exist") is None


class _GoodAdapter:
    name = "good"
    family = Family.COMMUNITY

    async def fetch(self, since: datetime) -> list[Item]:
        return [
            Item.create(source="good", family=self.family, title="A", url="https://e/a"),
            Item.create(source="good", family=self.family, title="B", url="https://e/b"),
        ]


class _DupAdapter:
    name = "dup"
    family = Family.COMMUNITY

    async def fetch(self, since: datetime) -> list[Item]:
        # Same url as _GoodAdapter's first item -> same content-hash id.
        return [
            Item.create(source="dup", family=self.family, title="A", url="https://e/a"),
        ]


class _CrashAdapter:
    name = "crash"
    family = Family.COMMUNITY

    async def fetch(self, since: datetime) -> list[Item]:
        raise RuntimeError("boom")


async def test_ingest_all_dedups_by_id(since: datetime) -> None:
    items = await registry.ingest_all(since, adapters=[_GoodAdapter(), _DupAdapter()])
    # 3 items produced but one is a duplicate id -> 2 unique
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids)) == 2


async def test_ingest_all_isolates_failures(since: datetime) -> None:
    items = await registry.ingest_all(
        since, adapters=[_GoodAdapter(), _CrashAdapter()]
    )
    # crash adapter contributes nothing; good adapter's 2 items survive
    assert len(items) == 2


async def test_ingest_all_empty_adapters(since: datetime) -> None:
    assert await registry.ingest_all(since, adapters=[]) == []
