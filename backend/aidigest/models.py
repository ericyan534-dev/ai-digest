"""The typed spine of ai-digest (Pydantic v2).

Every pipeline stage reads/writes these objects — never free text passed
model-to-model. All models are FROZEN (immutable): mutating helpers return
new copies via `model_copy(update=...)`.

Enums:
    Family         — the four source worlds (academia | industry | community | meta)
    ImportanceTier — the flexibility-principle gate (BREAKTHROUGH | NOTABLE | MINOR | QUIET_DAY)
    FeedbackSignal — kinds of feedback we record
    DigestKind     — daily | weekly

Core objects:
    Source, Item, Story, StorySummary, DailyDigest, WeeklyDigest, Feedback
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Family(str, Enum):
    """The four source families a story can belong to."""

    ACADEMIA = "academia"
    INDUSTRY = "industry"
    COMMUNITY = "community"
    META = "meta"


class ImportanceTier(str, Enum):
    """Importance gate driving how much the generator writes (flexibility principle).

    BREAKTHROUGH — truly revolutionary; go FULL DEPTH incl. background/context.
    NOTABLE      — meaningful; standard 2-4 sentence takeaway + why-it-matters.
    MINOR        — briefly summarize the trend in one line.
    QUIET_DAY    — nothing major; say so honestly ("Quiet day — nothing major shipped").
    """

    BREAKTHROUGH = "breakthrough"
    NOTABLE = "notable"
    MINOR = "minor"
    QUIET_DAY = "quiet_day"


class FeedbackSignal(str, Enum):
    """Kinds of feedback recorded against a target."""

    UP = "up"
    DOWN = "down"
    CLICK = "click"
    DWELL = "dwell"
    NL_INSTRUCTION = "nl_instruction"


class FeedbackTargetKind(str, Enum):
    """What a piece of feedback points at."""

    ITEM = "item"
    STORY = "story"
    DIGEST_SECTION = "digest_section"
    DIGEST = "digest"


class DigestKind(str, Enum):
    """The two products."""

    DAILY = "daily"
    WEEKLY = "weekly"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    return datetime.now(UTC)


def content_hash(*, url: str | None, text: str) -> str:
    """Deterministic content-hash id for an Item (idempotency / replayability).

    Uses the canonical url when present, else the normalized text. Returns a
    hex sha256 digest (64 chars).
    """
    basis = (url or "").strip().lower() or _normalize_text(text)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def slugify(value: str, *, max_len: int = 80) -> str:
    """Wikilink-friendly slug (stable IDs for the future Markdown/wiki exporter)."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_len] or "untitled"


# --------------------------------------------------------------------------- #
# Source registry record
# --------------------------------------------------------------------------- #


class Source(BaseModel):
    """A registered ingestion source (row in `sources`)."""

    model_config = ConfigDict(frozen=True)

    name: str  # stable adapter name, e.g. "arxiv", "hn", "blog:anthropic"
    family: Family
    url: str | None = None
    authority: float = Field(default=0.5, ge=0.0, le=1.0)  # source-authority weight
    enabled: bool = True
    config: dict = Field(default_factory=dict)  # adapter-specific options


# --------------------------------------------------------------------------- #
# Item — the normalized ingestion unit
# --------------------------------------------------------------------------- #


class Item(BaseModel):
    """One normalized piece of content. Content-hashed for idempotency.

    `id` = content_hash(url=..., text=raw_text) unless explicitly provided.
    """

    model_config = ConfigDict(frozen=True)

    id: str  # = sha256(canonical_url or normalized_text)
    source: str  # adapter name, e.g. "arxiv" | "reddit" | "hn" | "blog:anthropic"
    family: Family
    url: str | None = None
    title: str
    author: str | None = None
    published_at: datetime
    fetched_at: datetime = Field(default_factory=_utcnow)
    raw_text: str = ""  # cleaned body (markdown)
    embedding: list[float] | None = None  # filled in processing (len == EMBED_DIM)
    metrics: dict = Field(default_factory=dict)  # upvotes, comments, citations, hf_upvotes
    raw: dict = Field(default_factory=dict)  # source-specific payload, never lost

    @classmethod
    def create(
        cls,
        *,
        source: str,
        family: Family,
        title: str,
        url: str | None = None,
        author: str | None = None,
        published_at: datetime | None = None,
        raw_text: str = "",
        metrics: dict | None = None,
        raw: dict | None = None,
    ) -> Item:
        """Construct an Item with a derived content-hash id and sane defaults."""
        return cls(
            id=content_hash(url=url, text=f"{title}\n{raw_text}"),
            source=source,
            family=family,
            url=url,
            title=title,
            author=author,
            published_at=published_at or _utcnow(),
            raw_text=raw_text,
            metrics=metrics or {},
            raw=raw or {},
        )

    def with_embedding(self, embedding: list[float]) -> Item:
        """Return a copy carrying the embedding (immutable update)."""
        return self.model_copy(update={"embedding": embedding})


# --------------------------------------------------------------------------- #
# Story — a cluster of items (the unit the digest talks about)
# --------------------------------------------------------------------------- #


class Story(BaseModel):
    """A cluster of deduped Items forming one narrative unit."""

    model_config = ConfigDict(frozen=True)

    id: str  # stable id (e.g. slug + short hash)
    title: str
    family: Family
    item_ids: list[str] = Field(default_factory=list)
    representative_item_id: str | None = None
    embedding: list[float] | None = None  # centroid embedding (len == EMBED_DIM)
    importance: float = 0.0  # raw importance score (see process/rank.py)
    personal: float = 0.0  # cosine to interest vector
    final_rank: float = 0.0  # blended ranking score
    tier: ImportanceTier = ImportanceTier.MINOR  # importance gate (generate/importance.py)
    mention_count: int = 1  # cross-source mentions (a top relevance signal)
    # Objective attention signals carried from member items (set in cluster.py).
    # These feed importance/newsworthiness so a quiet day is judged by what the
    # world is actually reacting to, not static source authority.
    engagement: float = 0.0  # max normalized engagement across members (0..1)
    citation: float = 0.0  # max citation velocity across members (0..1)
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def slug(self) -> str:
        return slugify(self.title)


# --------------------------------------------------------------------------- #
# StorySummary — the map-step output that goes into a digest
# --------------------------------------------------------------------------- #


class StorySummary(BaseModel):
    """LLM-written, per-story takeaway used to assemble a digest section."""

    model_config = ConfigDict(frozen=True)

    story_id: str
    title: str
    family: Family
    tier: ImportanceTier
    takeaway: str  # 2-4 sentence summary (longer when tier == BREAKTHROUGH)
    why_it_matters: str  # personal angle tied to the user's subfields
    links: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # subfield tags
    score: float = 0.0  # carried-through final_rank for ordering/display


# --------------------------------------------------------------------------- #
# Digests
# --------------------------------------------------------------------------- #


class DigestSection(BaseModel):
    """A family-grouped section of a daily digest.

    ``intro`` is an optional trend-recap paragraph (smol.ai-style) shown before the
    items — used for the academia "research trends" and community "pulse" recaps so
    the digest summarizes a theme + links instead of listing every story in full.
    """

    model_config = ConfigDict(frozen=True)

    family: Family
    heading: str
    intro: str = ""
    summaries: list[StorySummary] = Field(default_factory=list)


class DailyDigest(BaseModel):
    """The short daily product. Carries quiet-day signal so it renders honestly."""

    model_config = ConfigDict(frozen=True)

    id: str  # e.g. "daily-2026-06-21"
    kind: DigestKind = DigestKind.DAILY
    date: str  # ISO date "YYYY-MM-DD"
    tldr: str  # one-line TL;DR of the day (honest on quiet days)
    overall_tier: ImportanceTier  # max tier across stories; QUIET_DAY when nothing shipped
    quiet_day: bool = False  # True => "Quiet day — nothing major shipped"
    sections: list[DigestSection] = Field(default_factory=list)
    story_ids: list[str] = Field(default_factory=list)
    model: str = ""  # generating model id
    cost_usd: float = 0.0
    eval_scores: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class WeeklyShortlistEntry(BaseModel):
    """One pick in the 'What I'd actually read this week' shortlist."""

    model_config = ConfigDict(frozen=True)

    title: str
    url: str | None = None
    one_liner: str
    family: Family


class WeeklyDigest(BaseModel):
    """The long weekly 'Week at a Glance' editorial (best-of-N + judge + polish)."""

    model_config = ConfigDict(frozen=True)

    id: str  # e.g. "weekly-2026-W25"
    kind: DigestKind = DigestKind.WEEKLY
    week_of: str  # ISO date of week start "YYYY-MM-DD"
    title: str  # editorial headline
    lede: str  # strong narrative opening
    body_markdown: str  # the full NYT-style editorial (markdown)
    overall_tier: ImportanceTier  # max tier across the week
    quiet_week: bool = False  # honest "quiet week" handling
    shortlist: list[WeeklyShortlistEntry] = Field(default_factory=list)  # what to read
    on_my_radar: list[WeeklyShortlistEntry] = Field(default_factory=list)  # academia preview
    story_ids: list[str] = Field(default_factory=list)
    candidate_count: int = 0  # N drafts generated
    winning_candidate: int = 0  # index of judge-selected draft
    model: str = ""
    judge_model: str = ""
    cost_usd: float = 0.0
    eval_scores: dict = Field(default_factory=dict)  # rubric scores from the judge
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #


class Feedback(BaseModel):
    """A single feedback event (👍/👎, click, dwell, or NL steering instruction)."""

    model_config = ConfigDict(frozen=True)

    id: str | None = None  # assigned by the DB on insert
    user: str = "me"  # single-user system
    target_id: str
    target_kind: FeedbackTargetKind
    signal: FeedbackSignal
    value: float = 1.0  # +1/-1 for up/down; seconds for dwell; 1.0 for click
    text: str | None = None  # NL instruction body when signal == NL_INSTRUCTION
    created_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    "Family",
    "ImportanceTier",
    "FeedbackSignal",
    "FeedbackTargetKind",
    "DigestKind",
    "content_hash",
    "slugify",
    "Source",
    "Item",
    "Story",
    "StorySummary",
    "DigestSection",
    "DailyDigest",
    "WeeklyShortlistEntry",
    "WeeklyDigest",
    "Feedback",
]
