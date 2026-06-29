"""Tests for process/dedup.py — near-duplicate collapse by embedding cosine.

MOCK mode. Uses ``embedded_items`` with *injected* embeddings (the mock embedder
produces random unit vectors that never cluster, so semantic grouping must be
driven by controlled vectors). The three cross-source 'DeepSeek' items share a
near-identical embedding (cosine >= 0.98) and MUST collapse into one cluster;
the two distinct items stay singletons.

Skipped until the PROCESS implementer lands ``aidigest.process.dedup``.
"""

from __future__ import annotations

import pytest

dedup_mod = pytest.importorskip("aidigest.process.dedup")
dedup = dedup_mod.dedup

from aidigest.models import Family, Item  # noqa: E402


def _cluster_of(clusters, predicate):
    """Return the cluster whose representative matches predicate (or None)."""
    for c in clusters:
        if predicate(c[0]):
            return c
    return None


def test_dedup_collapses_cross_source_duplicates(embedded_items: list[Item]) -> None:
    clusters = dedup(embedded_items, threshold=0.90)
    # 5 items -> 3 clusters (A collapses 3, B and C singletons).
    assert len(clusters) == 3
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 1, 3]


def test_dedup_representative_is_highest_authority(embedded_items: list[Item]) -> None:
    clusters = dedup(embedded_items, threshold=0.90)
    big = max(clusters, key=len)
    assert len(big) == 3
    # The representative (index 0) is the highest-authority/most-engaged member;
    # selection is deterministic and stable across runs.
    rep_first = big[0]
    clusters2 = dedup(list(reversed(embedded_items)), threshold=0.90)
    big2 = max(clusters2, key=len)
    assert big2[0].id == rep_first.id  # order-independent representative


def test_dedup_threshold_respected(embedded_items: list[Item]) -> None:
    # An impossibly high threshold prevents any merge -> all singletons.
    clusters = dedup(embedded_items, threshold=0.999999)
    assert len(clusters) == len(embedded_items)
    assert all(len(c) == 1 for c in clusters)


def test_dedup_items_without_embedding_are_singletons(now) -> None:
    a = Item.create(source="hn", family=Family.COMMUNITY, title="no embed 1")
    b = Item.create(source="hn", family=Family.COMMUNITY, title="no embed 2")
    clusters = dedup([a, b], threshold=0.90)
    assert len(clusters) == 2
    assert all(len(c) == 1 for c in clusters)


def test_dedup_empty_input() -> None:
    assert dedup([], threshold=0.90) == []


def test_dedup_is_pure_no_mutation(embedded_items: list[Item]) -> None:
    before_ids = [it.id for it in embedded_items]
    dedup(embedded_items, threshold=0.90)
    after_ids = [it.id for it in embedded_items]
    assert before_ids == after_ids  # input list/items untouched
