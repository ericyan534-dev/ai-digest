"""Near-duplicate detection over Item embeddings (DBSCAN-style threshold).

Groups items whose pairwise cosine similarity is >= threshold (default 0.90)
into clusters via single-link agglomeration (union-find). The
highest-authority/most-engaged item becomes index 0 (the representative).
Items lacking embeddings form singleton clusters and are never merged.
"""

from __future__ import annotations

from aidigest.models import Item
from aidigest.process._signals import representative_score
from aidigest.process._vec import cosine_matrix


def dedup(items: list[Item], *, threshold: float = 0.90) -> list[list[Item]]:
    """Group near-duplicate items by embedding cosine similarity (>= threshold).

    Returns clusters (each a list of Items) ordered by representative score; the
    representative (highest authority/engagement) is index 0 of each cluster.
    """
    if not items:
        return []

    embedded = [it for it in items if it.embedding is not None]
    unembedded = [it for it in items if it.embedding is None]

    clusters: list[list[Item]] = []
    if embedded:
        labels = _single_link_labels(
            [it.embedding for it in embedded if it.embedding is not None], threshold
        )
        grouped: dict[int, list[Item]] = {}
        for item, label in zip(embedded, labels, strict=True):
            grouped.setdefault(label, []).append(item)
        for members in grouped.values():
            clusters.append(_order_cluster(members))

    # Items without embeddings can't be compared => each is its own cluster.
    clusters.extend([[it] for it in unembedded])

    # Stable global ordering: strongest representative first.
    clusters.sort(key=lambda c: representative_score(c[0]), reverse=True)
    return clusters


def _single_link_labels(vectors: list[list[float]], threshold: float) -> list[int]:
    """Union-find over the similarity graph; returns a label per vector."""
    n = len(vectors)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    sim = cosine_matrix(vectors)
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                union(i, j)
    return [find(i) for i in range(n)]


def _order_cluster(members: list[Item]) -> list[Item]:
    """Representative (highest score) first; rest by descending score."""
    return sorted(members, key=representative_score, reverse=True)


__all__ = ["dedup"]
