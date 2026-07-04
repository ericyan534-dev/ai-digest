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


# Minimum Jaccard overlap of title tokens required for a cross-family pair that
# includes an ACADEMIA item to be considered "the same story". AI-topic embeddings
# are too compressed: a Reddit post about "BlockPilot" and an HF-papers entry for
# "Multi-Resolution Flow Matching" can both sit above the cluster threshold because
# both describe diffusion/attention efficiency work. Title overlap is a cheap,
# reliable secondary discriminator — the same paper mentioned in two sources will
# share most of its title words; two different papers almost never will.
_PAPER_TITLE_OVERLAP_MIN: float = 0.15


def _title_tokens(title: str) -> frozenset[str]:
    """Lowercase alphabetic tokens of length >= 3, minus common prepositions.

    Returns frozenset so callers can cheaply compute intersection/union.
    """
    _STOP = frozenset(
        {"the", "and", "for", "with", "via", "from", "this", "that", "are", "its"}
    )
    return frozenset(
        w
        for w in (t.lower() for t in re.findall(r"[A-Za-z]{3,}", title or ""))
        if w not in _STOP
    )


def _title_jaccard(a: Item, b: Item) -> float:
    """Jaccard coefficient on title tokens in [0, 1].

    Returns 0.0 when either item has an empty token set (too-short title to
    discriminate), so the cannot-link guard is not applied in that case.
    """
    ta = _title_tokens(a.title or "")
    tb = _title_tokens(b.title or "")
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


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
    3. When exactly one item in a cross-source pair is an ACADEMIA paper and the pair
       cannot be verified as the same paper via matching arXiv ids, title-token Jaccard
       below _PAPER_TITLE_OVERLAP_MIN blocks the merge. This prevents a Reddit post
       about paper Y from clustering with an HF/arXiv entry for paper X simply because
       both sit in the same hot sub-topic. A Reddit discussion of the SAME paper will
       share enough title words to clear the threshold; a genuinely different paper
       almost never does. Guard 3 is skipped when both items carry titles too short to
       tokenise (< 3-char words only) — those are test/stub items, not real papers.

    Cross-source pairs (different `source`) and cross-family pairs (a paper + its HN
    thread about the SAME paper) are unaffected — that is exactly the same-story-
    across-outlets case clustering exists to capture.
    """
    if a.family == Family.ACADEMIA and b.family == Family.ACADEMIA:
        id_a, id_b = _arxiv_id(a), _arxiv_id(b)
        return not (id_a and id_a == id_b)
    if a.source == b.source:
        pa, pb = _url_path(a.url), _url_path(b.url)
        if pa and pb and pa != pb:
            return True
    # Guard 3: one item is a paper, the other is not. Verify they describe the same
    # work before allowing the merge. If both carry the same arXiv id the ingestion
    # pipeline already confirmed the match — title check not needed. Otherwise fall
    # back to title overlap, which correctly blocks "BlockPilot" ↔ "Multi-Resolution
    # Flow Matching" while allowing "Multi-Resolution Flow Matching discussion" ↔ the
    # HF paper entry (Jaccard ≈ 0.58 >> 0.15).
    # The guard is SKIPPED when either item's title yields an empty token set (titles
    # shorter than 3 alphabetic chars, e.g. stub/test items) — 0.0 Jaccard on empty
    # sets is not a meaningful signal and must not block valid clustering.
    if a.family == Family.ACADEMIA or b.family == Family.ACADEMIA:
        id_a, id_b = _arxiv_id(a), _arxiv_id(b)
        if not (id_a and id_b and id_a == id_b):
            ta = _title_tokens(a.title or "")
            tb = _title_tokens(b.title or "")
            if ta and tb and len(ta & tb) / len(ta | tb) < _PAPER_TITLE_OVERLAP_MIN:
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
