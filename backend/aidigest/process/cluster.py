"""Group Items into Story objects by embedding similarity (COMPLETE-LINK).

Each Story groups items that are mutually similar (the SAME story across sources)
and carries mention_count = number of member items — the cross-source signal that
drives importance / the "breakthrough" tier.

Why complete-link (not single-link): AI-news embeddings are highly compressed
(measured on real data: unrelated items sit at ~0.71 median cosine, up to ~0.86),
so single-link CHAINS — at 0.75 it collapsed 51 unrelated items into one "story".
Complete-link merges two groups only when their WEAKEST cross-pair >= threshold,
so every member of a story is mutually >= threshold and distinct topics stay
apart. Default threshold 0.86 was tuned on real ingested+embedded items
(see profile.yaml `processing.cluster_threshold`).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

try:  # keep cluster importable without numpy (mock paths that never embed)
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None  # type: ignore[assignment]

from aidigest.models import Family, Item, Story, slugify
from aidigest.process._signals import (
    citation_velocity,
    engagement_score,
    representative_score,
)
from aidigest.process._vec import centroid, cosine_matrix


def _arxiv_id(item: Item) -> str | None:
    """The paper's arXiv id (from raw or an /abs/ URL), version-stripped."""
    aid = (item.raw or {}).get("arxiv_id")
    if aid:
        return str(aid).split("v")[0]
    url = item.url or ""
    if "arxiv.org/abs/" in url:
        return url.rsplit("/abs/", 1)[-1].split("v")[0]
    return None


def _url_path(url: str | None) -> str | None:
    """Scheme/host/query-stripped path key for same-source identity.

    Two URLs that differ only by host (old.reddit.com vs www.reddit.com) or by a
    tracking query map to the SAME key, while genuinely different articles keep
    different paths. Used to tell "the same post mirrored" from "two distinct pieces
    from one feed". Returns None for a urlless item.
    """
    if not url:
        return None
    s = re.sub(r"^https?://", "", url.strip().lower())
    s = s.split("#", 1)[0].split("?", 1)[0]
    slash = s.find("/")
    path = s[slash:].rstrip("/") if slash != -1 else ""
    return path or s


def _cannot_link(a: Item, b: Item) -> bool:
    """Block two items from clustering into one story when they are provably distinct.

    1. Two academia papers merge only if they are the SAME paper (same arXiv id):
       embedding space is compressed enough that distinct papers on a hot topic
       exceed the cosine threshold, so clustering them loses real papers and
       fabricates bundled titles.
    2. Two items from the SAME source with DIFFERENT canonical paths are distinct
       articles — one feed does not publish the same story under two paths. Merging
       them bundles e.g. three separate TechCrunch funding pieces under one (wrong)
       title. `mention_count` is a CROSS-source corroboration signal, so within-source
       items must never merge to inflate it.

    Cross-source pairs (different `source`) and cross-family pairs (a paper + its HN
    thread) are unaffected — that is exactly the same-story-across-outlets case
    clustering exists to capture.
    """
    if a.family == Family.ACADEMIA and b.family == Family.ACADEMIA:
        id_a, id_b = _arxiv_id(a), _arxiv_id(b)
        return not (id_a and id_a == id_b)
    if a.source == b.source:
        pa, pb = _url_path(a.url), _url_path(b.url)
        if pa and pb and pa != pb:
            return True
    return False


def story_id(title: str, item_ids: list[str]) -> str:
    """Stable story id = slugify(title) + '-' + short hash of sorted item ids."""
    basis = "|".join(sorted(item_ids))
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8]
    return f"{slugify(title)}-{digest}"


def cluster_into_stories(items: list[Item], *, threshold: float = 0.86) -> list[Story]:
    """Cluster items into Story objects via complete-link agglomeration.

    A story's members are mutually >= `threshold` (so distinct topics never chain
    together); mention_count = group size captures the cross-source signal.
    """
    if not items:
        return []

    embedded = [it for it in items if it.embedding is not None]
    unembedded = [it for it in items if it.embedding is None]

    groups: list[list[Item]] = []
    if embedded:
        vectors = [it.embedding for it in embedded if it.embedding is not None]
        sim = cosine_matrix(vectors)
        # Block distinct academia papers from merging (different/absent arXiv ids).
        if _np is not None:
            for i in range(len(embedded)):
                for j in range(i + 1, len(embedded)):
                    if _cannot_link(embedded[i], embedded[j]):
                        sim[i, j] = sim[j, i] = -1.0
        index_groups = _complete_link_groups(sim, threshold)
        groups.extend([[embedded[i] for i in g] for g in index_groups])

    # Items without embeddings become singleton stories (never dropped).
    groups.extend([[it] for it in unembedded])

    stories = [_build_story(group) for group in groups]
    stories.sort(key=lambda s: s.mention_count, reverse=True)
    return stories


def _build_story(members: list[Item]) -> Story:
    ordered = sorted(members, key=representative_score, reverse=True)
    rep = ordered[0]
    item_ids = [it.id for it in ordered]
    family = _modal_family(ordered)
    vectors = [it.embedding for it in ordered if it.embedding is not None]
    return Story(
        id=story_id(rep.title, item_ids),
        title=rep.title,
        family=family,
        item_ids=item_ids,
        representative_item_id=rep.id,
        embedding=centroid(vectors),
        mention_count=len(ordered),
        engagement=max((engagement_score(m) for m in ordered), default=0.0),
        citation=max((citation_velocity(m) for m in ordered), default=0.0),
    )


def _modal_family(members: list[Item]) -> Family:
    """Most common family among members (ties broken by first occurrence)."""
    counts = Counter(it.family for it in members)
    return counts.most_common(1)[0][0]


def _complete_link_groups(sim: Any, threshold: float) -> list[list[int]]:
    """Complete-link agglomeration over a precomputed similarity matrix `sim`.

    Returns index groups whose every pair is >= threshold. Greedily merges the two
    groups with the highest *minimum* cross-pair similarity until none reach the
    threshold (so no chaining). Blocked pairs (sim == -1) never merge.

    O(n^3) worst case; fine for per-day item volumes (tens–low hundreds).
    """
    n = int(sim.shape[0])
    if n == 0:
        return []
    groups: list[list[int]] = [[i] for i in range(n)]
    while len(groups) > 1:
        best = -1.0
        best_a = best_b = -1
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                # complete-link distance => weakest cross-pair similarity.
                weakest = min(sim[i, j] for i in groups[a] for j in groups[b])
                if weakest > best:
                    best, best_a, best_b = float(weakest), a, b
        if best < threshold:
            break
        groups[best_a].extend(groups[best_b])
        del groups[best_b]
    return groups


__all__ = ["cluster_into_stories", "story_id"]
