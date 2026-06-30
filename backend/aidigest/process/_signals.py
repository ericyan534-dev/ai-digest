"""Engagement / authority / recency signal extraction from Item metrics.

Pure functions over an Item's `metrics` dict + source family. Reused by dedup
(representative selection) and rank (importance scoring). All outputs are in a
roughly comparable 0..1+ range before weighting.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from aidigest.models import Family, Item

# A concrete release / announcement (a real model/product/result shipping) — the
# kind of title that legitimizes a high-engagement single-source story as genuinely
# important. A viral anecdote/drama title ("my resume scored 90/100", "second
# opinion on my MRI") matches NONE of these. Mirrors generate.daily._ANNOUNCE_RE.
_RELEASE_TITLE_RE = re.compile(
    r"\b(unveils?|introduc\w+|releas\w+|launch\w+|announc\w+|ships?|debuts?|"
    r"open[- ]?sourc\w+|presents?|now available)\b",
    re.IGNORECASE,
)


def is_release_title(title: str) -> bool:
    """True when a title reads as a concrete release/announcement (not an anecdote)."""
    return bool(_RELEASE_TITLE_RE.search(title or ""))

# Default per-family source authority (overridable via profile family_weights).
_FAMILY_AUTHORITY: dict[Family, float] = {
    Family.ACADEMIA: 0.9,
    Family.INDUSTRY: 0.8,
    Family.META: 0.7,
    Family.COMMUNITY: 0.6,
}

# Known high-authority sources get a small bump regardless of family.
_SOURCE_AUTHORITY: dict[str, float] = {
    "arxiv": 0.85,
    "openreview": 0.9,
    "semantic_scholar": 0.85,
    "hf_papers": 0.8,
    "smol.ai": 0.8,
    "hn": 0.65,
    "reddit": 0.55,
}


# Engagement at which a story reads as "the big one" of the day (~HN front-page
# blockbuster). Power-curve below DISCRIMINATES viral from routine: a 2.5k-point
# launch -> ~1.0; a routine 300-point post -> ~0.3; a 50-point post -> ~0.1. (Log
# scaling compressed these together, which under-detected genuine breakthroughs.)
_VIRAL_ENGAGEMENT = 2500.0


def engagement_score(item: Item) -> float:
    """Normalized engagement from upvotes/comments/hf_upvotes (0..1, viral-aware)."""
    m = item.metrics or {}
    upvotes = _num(m, "upvotes") + _num(m, "score") + _num(m, "hf_upvotes")
    comments = _num(m, "comments") + _num(m, "num_comments")
    raw = upvotes + 0.5 * comments
    if raw <= 0:
        return 0.0
    return min(1.0, (raw / _VIRAL_ENGAGEMENT) ** 0.6)


def citation_velocity(item: Item) -> float:
    """Citations-per-day proxy, normalized to 0..1. Academia signal."""
    m = item.metrics or {}
    citations = _num(m, "citations") + _num(m, "citationCount")
    if citations <= 0:
        return 0.0
    age = max(age_days(item), 1.0)
    per_day = citations / age
    return min(1.0, math.log1p(per_day) / math.log1p(5.0))


def authority(item: Item) -> float:
    """Source authority in 0..1: max of family default and known-source value."""
    fam = _FAMILY_AUTHORITY.get(item.family, 0.5)
    src = _SOURCE_AUTHORITY.get(item.source, 0.0)
    return max(fam, src)


def recency_score(item: Item, *, half_life_days: float = 2.0) -> float:
    """Exponential recency decay; 1.0 at publish time, 0.5 after half_life_days."""
    age = age_days(item)
    if half_life_days <= 0:
        return 1.0
    return float(0.5 ** (age / half_life_days))


def age_days(item: Item) -> float:
    published = item.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - published
    return max(delta.total_seconds() / 86400.0, 0.0)


def representative_score(item: Item) -> float:
    """Combined score for picking a cluster representative.

    Engagement leads (the most-discussed item should front the cluster), with
    source authority and citation velocity as secondary/tertiary boosts. Citation
    velocity is down-weighted so a single same-day citation can't outrank a
    genuinely high-engagement post.
    """
    return (
        2.0 * engagement_score(item)
        + 1.0 * authority(item)
        + 0.5 * citation_velocity(item)
    )


def _num(d: dict, key: str) -> float:
    val = d.get(key)
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "engagement_score",
    "citation_velocity",
    "authority",
    "recency_score",
    "age_days",
    "representative_score",
    "is_release_title",
]
