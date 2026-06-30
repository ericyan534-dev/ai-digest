"""Shared pytest fixtures for ai-digest (all offline / MOCK LLM, no network).

Forces ``AIDIGEST_LLM_MOCK=1`` before any aidigest import so no test ever
touches the network or needs a key. Provides:

  * ``settings`` / ``llm`` / ``now`` — basic context.
  * ``sample_items`` / ``sample_stories`` — small hand-made fixtures.
  * ``embedded_items`` — items carrying *controlled* embeddings so dedup /
    cluster / rank are deterministic in MOCK mode (the mock embedder produces
    random unit vectors that never cluster semantically — tests must not rely on
    semantic similarity, so we inject known vectors instead).
  * golden-set fixtures (``busy_items`` / ``quiet_items`` + their metadata).
  * ``fake_repo`` — a fully in-memory async ``Repo`` stub satisfying the
    ``aidigest.db.repo.Repo`` interface, so API/repo tests run with NO Postgres.

The fake repo mirrors the INTERFACES.md ``Repo`` contract exactly (method names,
signatures, return shapes) so swapping it for the real psycopg-backed repo is a
no-op for the callers under test.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

# Hermetic tests: force delivery/auth secrets EMPTY so a populated real .env
# (live Resend/Telegram/Reddit keys) never leaks into the suite. Env vars take
# precedence over the .env file in pydantic-settings, so "" disables each channel.
for _secret in (
    "RESEND_API_KEY",
    "DIGEST_FROM_EMAIL",
    "DIGEST_TO_EMAIL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_WEBHOOK_SECRET",
    "AIDIGEST_API_KEY",
    "AIDIGEST_LINK_SECRET",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "S2_API_KEY",
):
    os.environ[_secret] = ""

from aidigest.config import Settings  # noqa: E402
from aidigest.eval.golden import golden_items, load_golden  # noqa: E402
from aidigest.llm.mock import MockLLMClient  # noqa: E402
from aidigest.models import (  # noqa: E402
    DailyDigest,
    Family,
    Item,
    Story,
)

EMBED_DIM = 1536


# --------------------------------------------------------------------------- #
# Basic context fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings() -> Settings:
    return Settings(AIDIGEST_LLM_MOCK=True)  # type: ignore[call-arg]


@pytest.fixture
def llm() -> MockLLMClient:
    return MockLLMClient(embed_dim=EMBED_DIM)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_items(now: datetime) -> list[Item]:
    return [
        Item.create(
            source="hn",
            family=Family.COMMUNITY,
            title="DeepSeek V4 released",
            url="https://news.ycombinator.com/item?id=1",
            raw_text="DeepSeek V4 ships a new RL post-training recipe.",
            published_at=now,
            metrics={"upvotes": 800, "comments": 300},
        ),
        Item.create(
            source="arxiv",
            family=Family.ACADEMIA,
            title="Efficient attention for long context",
            url="https://arxiv.org/abs/2606.00001",
            raw_text="A new linear attention variant for scalable NLP.",
            published_at=now,
            metrics={"citations": 3},
        ),
    ]


@pytest.fixture
def sample_stories(now: datetime) -> list[Story]:
    return [
        Story(
            id="deepseek-v4-ab12cd",
            title="DeepSeek V4 released",
            family=Family.INDUSTRY,
            item_ids=["a"],
            representative_item_id="a",
            importance=0.9,
            personal=0.8,
            final_rank=0.88,
            mention_count=7,
            created_at=now,
        ),
        Story(
            id="quiet-thing-ef34gh",
            title="A minor library bump",
            family=Family.COMMUNITY,
            item_ids=["b"],
            representative_item_id="b",
            importance=0.2,
            personal=0.1,
            final_rank=0.18,
            mention_count=1,
            created_at=now,
        ),
    ]


# --------------------------------------------------------------------------- #
# Controlled embeddings — deterministic dedup/cluster/rank in MOCK mode
# --------------------------------------------------------------------------- #


def unit_basis_vector(index: int, *, dim: int = EMBED_DIM) -> list[float]:
    """A one-hot unit vector at ``index`` — distinct indices are orthogonal
    (cosine 0); the same index is identical (cosine 1). Lets tests force exact
    cluster membership without relying on the mock embedder's random vectors.
    """
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


def blended_vector(primary: int, secondary: int, *, w: float = 0.97,
                    dim: int = EMBED_DIM) -> list[float]:
    """Mostly-``primary`` vector with a small ``secondary`` component, L2-normalized.

    Cosine to ``unit_basis_vector(primary)`` ≈ ``w`` — useful to place an item
    just above/below a dedup/cluster threshold deterministically.
    """
    vec = [0.0] * dim
    vec[primary % dim] = w
    vec[secondary % dim] = (1.0 - w * w) ** 0.5
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture
def embedded_items(now: datetime) -> list[Item]:
    """Three near-identical 'DeepSeek' items (cluster A, cross-source) + two
    distinct items (clusters B and C). Embeddings are injected so dedup (>=0.90)
    collapses the three A's and cluster (>=0.75) keeps A/B/C separate.
    """
    a1 = Item.create(
        source="hn", family=Family.COMMUNITY, title="DeepSeek V4 released",
        url="https://news.ycombinator.com/item?id=10", raw_text="DeepSeek V4 RL recipe.",
        published_at=now, metrics={"upvotes": 1200, "comments": 500},
    ).with_embedding(unit_basis_vector(0))
    a2 = Item.create(
        source="arxiv", family=Family.ACADEMIA, title="DeepSeek V4: RL post-training",
        url="https://arxiv.org/abs/2606.01001", raw_text="DeepSeek V4 paper.",
        published_at=now, metrics={"citations": 12},
    ).with_embedding(blended_vector(0, 7, w=0.99))  # cosine ~0.99 to a1 -> dedup
    a3 = Item.create(
        source="rss:smol.ai", family=Family.META, title="AINews: DeepSeek V4 is the big one",
        url="https://smol.ai/news/deepseek-v4", raw_text="The big one: DeepSeek V4.",
        published_at=now, metrics={"upvotes": 300},
    ).with_embedding(blended_vector(0, 9, w=0.98))  # cosine ~0.98 to a1 -> dedup
    b = Item.create(
        source="arxiv", family=Family.ACADEMIA, title="Linear attention for long context",
        url="https://arxiv.org/abs/2606.02002", raw_text="Efficient scalable NLP attention.",
        published_at=now, metrics={"citations": 4},
    ).with_embedding(unit_basis_vector(100))
    c = Item.create(
        source="reddit", family=Family.COMMUNITY, title="Multi-agent framework 1.0",
        url="https://reddit.com/r/ML/comments/x", raw_text="Multi-agent systems tooling.",
        published_at=now, metrics={"upvotes": 220, "comments": 60},
    ).with_embedding(unit_basis_vector(200))
    return [a1, a2, a3, b, c]


# --------------------------------------------------------------------------- #
# Golden-set fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def busy_meta() -> dict:
    return load_golden("busy_day")


@pytest.fixture
def quiet_meta() -> dict:
    return load_golden("quiet_day")


@pytest.fixture
def busy_items() -> list[Item]:
    return golden_items("busy_day")


@pytest.fixture
def quiet_items() -> list[Item]:
    return golden_items("quiet_day")


@pytest.fixture
def busy_stories(busy_items: list[Item], now: datetime) -> list[Story]:
    """Stories approximating the busy-day universe: one BREAKTHROUGH (DeepSeek,
    cross-source, high rank) plus NOTABLE/MINOR ones. Built directly (not via the
    PROCESS clusterer) so generate/importance tests don't depend on that module.
    """
    ds_ids = [busy_items[0].id, busy_items[1].id, busy_items[2].id]
    return [
        Story(
            id="deepseek-v4-breakthrough", title="DeepSeek V4 released",
            family=Family.ACADEMIA, item_ids=ds_ids, representative_item_id=ds_ids[0],
            importance=0.95, personal=0.92, final_rank=0.94, mention_count=3,
            created_at=now,
        ),
        Story(
            id="linear-attention-notable", title="Linear attention for long context",
            family=Family.ACADEMIA, item_ids=[busy_items[3].id],
            representative_item_id=busy_items[3].id,
            importance=0.6, personal=0.65, final_rank=0.62, mention_count=1,
            created_at=now,
        ),
        Story(
            id="multi-agent-minor", title="Multi-agent framework 1.0",
            family=Family.COMMUNITY, item_ids=[busy_items[4].id],
            representative_item_id=busy_items[4].id,
            importance=0.34, personal=0.4, final_rank=0.36, mention_count=1,
            created_at=now,
        ),
    ]


@pytest.fixture
def quiet_stories(quiet_items: list[Item], now: datetime) -> list[Story]:
    """All-low-rank stories => a quiet day (top score below quiet_day_top_score)."""
    return [
        Story(
            id=f"quiet-{i}", title=it.title, family=it.family,
            item_ids=[it.id], representative_item_id=it.id,
            importance=0.18, personal=0.12, final_rank=0.15, mention_count=1,
            created_at=now,
        )
        for i, it in enumerate(quiet_items)
    ]


@pytest.fixture
def profile() -> dict:
    """The seed profile loaded from backend/profile.yaml (single source of truth
    for ranking weights + tier thresholds). Falls back to a literal if the
    personalize loader is not yet present.
    """
    try:
        from aidigest.personalize.profile import load_profile

        return load_profile()
    except Exception:
        from pathlib import Path

        import yaml

        from aidigest.config import get_settings  # noqa: F401

        path = Path(__file__).resolve().parents[1] / "profile.yaml"
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)


@pytest.fixture
def interest_vector() -> list[float]:
    """A deterministic interest vector (unit length) for rank tests."""
    return unit_basis_vector(0)


# --------------------------------------------------------------------------- #
# In-memory fake Repo — runs API/repo tests with NO Postgres
# --------------------------------------------------------------------------- #


class FakeRepo:
    """In-memory async stand-in for ``aidigest.db.repo.Repo``.

    Implements the full INTERFACES.md surface with dict/list storage. Used by
    API + flow tests so they never need a live database. Returns the same shapes
    the real repo promises (e.g. ``list_digests`` lightweight rows).
    """

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn
        self._items: dict[str, Item] = {}
        self._stories: dict[str, Story] = {}
        self._digests: dict[str, object] = {}
        self._feedback: list = []
        self._sources: dict[str, object] = {}
        self._eval_runs: list[dict] = []
        self._app_state: dict[str, dict] = {}
        self._fb_id = 0
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def init_schema(self) -> None:
        return None

    # --- items ---
    async def upsert_items(self, items: list) -> int:
        for it in items:
            self._items[it.id] = it
        return len(items)

    async def get_items_since(self, since: datetime, *, family=None) -> list:
        out = [
            it for it in self._items.values()
            if it.published_at >= since and (family is None or it.family == family)
        ]
        return out

    async def get_items_by_ids(self, ids: list[str]) -> list:
        return [self._items[i] for i in ids if i in self._items]

    async def get_items_without_embedding(self, limit: int = 500) -> list:
        return [it for it in self._items.values() if it.embedding is None][:limit]

    async def set_item_embedding(self, item_id: str, embedding: list[float]) -> None:
        if item_id in self._items:
            self._items[item_id] = self._items[item_id].with_embedding(embedding)

    # --- stories ---
    async def upsert_stories(self, stories: list) -> int:
        for s in stories:
            self._stories[s.id] = s
        return len(stories)

    async def get_stories_for_date(self, date: str) -> list:
        # Mirror the real repo: bucket by the configured LOCAL day, not UTC.
        from zoneinfo import ZoneInfo

        from aidigest.config import get_settings

        tz = ZoneInfo(get_settings().timezone)

        def _local_date(s) -> str:
            ca = s.created_at
            if ca.tzinfo is None:
                ca = ca.replace(tzinfo=UTC)
            return ca.astimezone(tz).date().isoformat()

        return [s for s in self._stories.values() if _local_date(s) == date]

    async def delete_stories_for_date(self, date: str) -> int:
        for_date = {s.id for s in await self.get_stories_for_date(date)}
        self._stories = {sid: s for sid, s in self._stories.items() if sid not in for_date}
        return len(for_date)

    async def get_stories_by_ids(self, ids: list[str]) -> list:
        return [self._stories[i] for i in ids if i in self._stories]

    # --- digests ---
    async def save_daily(self, digest) -> None:
        self._digests[digest.id] = digest

    async def save_weekly(self, digest) -> None:
        self._digests[digest.id] = digest

    async def get_digest(self, digest_id: str):
        return self._digests.get(digest_id)

    async def list_digests(self, *, kind=None, limit: int = 30) -> list[dict]:
        rows: list[dict] = []
        for d in self._digests.values():
            dkind = getattr(d, "kind", None)
            kind_val = getattr(dkind, "value", dkind)
            if kind is not None and kind_val != getattr(kind, "value", kind):
                continue
            is_daily = kind_val == "daily"
            rows.append(
                {
                    "id": d.id,
                    "kind": kind_val,
                    "date": getattr(d, "date", getattr(d, "week_of", "")),
                    "tier": d.overall_tier.value,
                    "quiet": getattr(d, "quiet_day", getattr(d, "quiet_week", False)),
                    "title": d.tldr if is_daily else d.title,
                    "created_at": d.created_at.isoformat(),
                }
            )
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    # --- feedback ---
    async def add_feedback(self, fb):
        self._fb_id += 1
        stored = fb.model_copy(update={"id": str(self._fb_id)})
        self._feedback.append(stored)
        return stored

    async def get_feedback(self, *, signal=None, since=None) -> list:
        out = list(self._feedback)
        if signal is not None:
            out = [f for f in out if f.signal == signal]
        if since is not None:
            out = [f for f in out if f.created_at >= since]
        return out

    # --- sources ---
    async def upsert_sources(self, sources: list) -> int:
        for s in sources:
            self._sources[s.name] = s
        return len(sources)

    async def get_sources(self, *, enabled_only: bool = True) -> list:
        out = list(self._sources.values())
        if enabled_only:
            out = [s for s in out if getattr(s, "enabled", True)]
        return out

    # --- eval ---
    async def save_eval_run(self, *, digest_id: str, judge_model: str,
                            scores: dict, notes: str | None = None) -> None:
        self._eval_runs.append(
            {"digest_id": digest_id, "judge_model": judge_model,
             "scores": scores, "notes": notes}
        )

    # --- app_state (interest vector / profile override) ---
    async def save_app_state(self, key: str, value: dict) -> None:
        self._app_state[key] = dict(value)

    async def get_app_state(self, key: str) -> dict | None:
        return self._app_state.get(key)

    async def save_interest_vector(self, vector: list[float]) -> None:
        self._app_state["interest_vector"] = {"vector": list(vector), "dim": len(vector)}

    async def get_interest_vector(self) -> list[float] | None:
        state = self._app_state.get("interest_vector")
        return list(state["vector"]) if state and "vector" in state else None

    async def save_profile_override(self, profile: dict) -> None:
        self._app_state["profile_override"] = dict(profile)

    async def get_profile_override(self) -> dict | None:
        return self._app_state.get("profile_override")

    # --- vector search ---
    async def similar_items(self, embedding: list[float], *, k: int = 20,
                            since: datetime | None = None) -> list[tuple]:
        def cos(a: list[float], b: list[float]) -> float:
            num = sum(x * y for x, y in zip(a, b, strict=False))
            na = sum(x * x for x in a) ** 0.5 or 1.0
            nb = sum(x * x for x in b) ** 0.5 or 1.0
            return num / (na * nb)

        scored: list[tuple] = []
        for it in self._items.values():
            if it.embedding is None:
                continue
            if since is not None and it.published_at < since:
                continue
            scored.append((it, cos(embedding, it.embedding)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


@pytest.fixture
def fake_repo() -> FakeRepo:
    return FakeRepo()


# --------------------------------------------------------------------------- #
# Ready-made digest fixtures (for render / api tests)
# --------------------------------------------------------------------------- #


def _summary(
    *, story_id: str, title: str, family: Family, tier, takeaway: str,
    links=None, tags=None, score: float = 0.7,
):
    from aidigest.models import StorySummary

    return StorySummary(
        story_id=story_id, title=title, family=family, tier=tier,
        takeaway=takeaway, why_it_matters="Ties to your subfields (RL for NLP).",
        links=links or [], tags=tags or [], score=score,
    )


@pytest.fixture
def busy_daily(now: datetime) -> DailyDigest:
    """A non-quiet daily digest with a BREAKTHROUGH + sections across families."""
    from aidigest.models import DigestSection, ImportanceTier

    sections = [
        DigestSection(
            family=Family.ACADEMIA, heading="🎓 Academia",
            summaries=[
                _summary(
                    story_id="deepseek-v4-ab12cd", title="DeepSeek V4 released",
                    family=Family.ACADEMIA, tier=ImportanceTier.BREAKTHROUGH,
                    takeaway=("DeepSeek V4 ships a new RL post-training recipe with "
                              "multi-agent self-play. Open weights. Resets the "
                              "open-model frontier on reasoning. Full background "
                              "and context included because this one matters."),
                    links=["https://arxiv.org/abs/2606.01001"],
                    tags=["LLMs", "RL for NLP", "Optimization"], score=0.94,
                ),
            ],
        ),
        DigestSection(
            family=Family.COMMUNITY, heading="💬 Community",
            summaries=[
                _summary(
                    story_id="multi-agent-minor", title="Multi-agent framework 1.0",
                    family=Family.COMMUNITY, tier=ImportanceTier.MINOR,
                    takeaway="A multi-agent framework hit 1.0.",
                    tags=["Multi-Agent Systems"], score=0.36,
                ),
            ],
        ),
    ]
    return DailyDigest(
        id="daily-2026-06-21", date="2026-06-21",
        tldr="DeepSeek V4 resets the open-model frontier; a quiet rest of the board.",
        overall_tier=ImportanceTier.BREAKTHROUGH, quiet_day=False,
        sections=sections,
        story_ids=["deepseek-v4-ab12cd", "multi-agent-minor"],
        model="mock-flash", created_at=now,
    )


@pytest.fixture
def quiet_daily(now: datetime) -> DailyDigest:
    """An honest quiet-day daily digest."""
    from aidigest.models import ImportanceTier

    return DailyDigest(
        id="daily-2026-06-22", date="2026-06-22",
        tldr="Quiet day — nothing major shipped.",
        overall_tier=ImportanceTier.QUIET_DAY, quiet_day=True,
        sections=[], story_ids=[], model="mock-flash", created_at=now,
    )


@pytest.fixture
def sample_weekly(now: datetime):
    from aidigest.models import ImportanceTier, WeeklyDigest, WeeklyShortlistEntry

    return WeeklyDigest(
        id="weekly-2026-W25", week_of="2026-06-15",
        title="The week reasoning got cheap",
        lede="A strong, plain narrative opening about the week.",
        body_markdown="# The week reasoning got cheap\n\nDeepSeek V4 led the week.\n",
        overall_tier=ImportanceTier.BREAKTHROUGH, quiet_week=False,
        shortlist=[
            WeeklyShortlistEntry(
                title="DeepSeek V4 paper", url="https://arxiv.org/abs/2606.01001",
                one_liner="The RL post-training recipe to actually read.",
                family=Family.ACADEMIA,
            ),
        ],
        on_my_radar=[
            WeeklyShortlistEntry(
                title="Linear attention for long context",
                url="https://arxiv.org/abs/2606.02002",
                one_liner="Efficient NLP preview worth tracking.",
                family=Family.ACADEMIA,
            ),
        ],
        story_ids=["deepseek-v4-ab12cd"],
        candidate_count=3, winning_candidate=1,
        model="mock-flash", judge_model="mock-flash",
        eval_scores={"insight": 4.2, "accuracy": 4.6, "narrative": 4.0,
                     "personal_fit": 4.3, "honesty": 5.0},
        created_at=now,
    )
