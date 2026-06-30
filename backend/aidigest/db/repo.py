"""Repository pattern over psycopg v3 (async) for ai-digest.

All persistence goes through `Repo`. The DSN defaults to
``get_settings().database_url``. On connect we register the pgvector type
adapter so ``vector(1536)`` columns round-trip as ``list[float]``.

psycopg + pgvector are imported lazily inside ``connect()`` so this module
imports cleanly in environments without those native packages (mock/CI paths
that never touch the DB).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aidigest.config import get_settings
from aidigest.db import _rows
from aidigest.models import (
    DailyDigest,
    DigestKind,
    Family,
    Feedback,
    FeedbackSignal,
    Item,
    Source,
    Story,
    WeeklyDigest,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psycopg import AsyncConnection
    from psycopg_pool import AsyncConnectionPool

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class Repo:
    """Async repository. Open with ``connect()``, close with ``close()``."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or get_settings().database_url
        self._pool: AsyncConnectionPool | None = None

    # ------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        """Open an async connection pool and register the pgvector adapter."""
        if self._pool is not None:
            return
        from psycopg_pool import AsyncConnectionPool  # lazy

        # Bootstrap: the pool's `configure` callback registers the pgvector type
        # adapter on EVERY connection, which fails ("vector type not found") on a
        # brand-new database where the extension isn't installed yet — so the pool
        # could never open to run init_schema. Ensure the extension exists first on a
        # one-off raw connection so a fresh deploy (e.g. a new Supabase project)
        # self-heals instead of dead-locking on this chicken-and-egg.
        await self._ensure_vector_extension()

        async def _configure(conn: AsyncConnection) -> None:
            from pgvector.psycopg import register_vector_async

            await register_vector_async(conn)

        self._pool = AsyncConnectionPool(
            self._dsn, min_size=1, max_size=10, configure=_configure, open=False
        )
        await self._pool.open(wait=True)

    async def _ensure_vector_extension(self) -> None:
        """Create the pgvector extension if missing (idempotent), on a raw connection
        that does NOT register the vector adapter (so it works before the type exists).

        No explicit schema: vanilla Postgres lands it in the current schema, and
        managed Postgres (Supabase) keeps any existing install in its `extensions`
        schema — both end up on the search_path so the type resolves.
        """
        import psycopg  # lazy — keep the module importable without native deps

        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await conn.close()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Repo is not connected; call await repo.connect() first.")
        return self._pool

    async def init_schema(self) -> None:
        """Apply db/schema.sql idempotently (all statements are CREATE ... IF NOT EXISTS)."""
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        async with self._require_pool().connection() as conn:
            await conn.execute(sql)  # type: ignore[arg-type]

    # ----------------------------------------------------------------- items
    async def upsert_items(self, items: list[Item]) -> int:
        if not items:
            return 0
        sql = """
            INSERT INTO items (id, source, family, url, title, author, published_at,
                               fetched_at, raw_text, embedding, metrics, raw)
            VALUES (%(id)s, %(source)s, %(family)s, %(url)s, %(title)s, %(author)s,
                    %(published_at)s, %(fetched_at)s, %(raw_text)s, %(embedding)s,
                    %(metrics)s, %(raw)s)
            ON CONFLICT (id) DO UPDATE SET
                source=EXCLUDED.source, family=EXCLUDED.family, url=EXCLUDED.url,
                title=EXCLUDED.title, author=EXCLUDED.author,
                published_at=EXCLUDED.published_at, fetched_at=EXCLUDED.fetched_at,
                raw_text=EXCLUDED.raw_text,
                embedding=COALESCE(EXCLUDED.embedding, items.embedding),
                metrics=EXCLUDED.metrics, raw=EXCLUDED.raw
        """
        async with self._require_pool().connection() as conn:
            async with conn.cursor() as cur:
                for item in items:
                    await cur.execute(sql, _jsonify(_rows.item_to_params(item)))
        return len(items)

    async def get_items_since(
        self, since: datetime, *, family: Family | None = None
    ) -> list[Item]:
        sql = "SELECT * FROM items WHERE published_at >= %s"
        params: list[Any] = [since]
        if family is not None:
            sql += " AND family = %s"
            params.append(family.value)
        sql += " ORDER BY published_at DESC"
        return [_rows.row_to_item(r) for r in await self._fetch_dicts(sql, params)]

    async def get_items_by_ids(self, ids: list[str]) -> list[Item]:
        if not ids:
            return []
        rows = await self._fetch_dicts("SELECT * FROM items WHERE id = ANY(%s)", [ids])
        return [_rows.row_to_item(r) for r in rows]

    async def get_items_without_embedding(self, limit: int = 500) -> list[Item]:
        sql = "SELECT * FROM items WHERE embedding IS NULL ORDER BY published_at DESC LIMIT %s"
        return [_rows.row_to_item(r) for r in await self._fetch_dicts(sql, [limit])]

    async def set_item_embedding(self, item_id: str, embedding: list[float]) -> None:
        async with self._require_pool().connection() as conn:
            await conn.execute(
                "UPDATE items SET embedding = %s WHERE id = %s",
                (_vec(embedding), item_id),
            )

    # --------------------------------------------------------------- stories
    async def upsert_stories(self, stories: list[Story]) -> int:
        if not stories:
            return 0
        story_sql = """
            INSERT INTO stories (id, title, family, representative_item_id, embedding,
                                 importance, personal, final_rank, tier, mention_count, created_at)
            VALUES (%(id)s, %(title)s, %(family)s, %(representative_item_id)s, %(embedding)s,
                    %(importance)s, %(personal)s, %(final_rank)s, %(tier)s,
                    %(mention_count)s, %(created_at)s)
            ON CONFLICT (id) DO UPDATE SET
                title=EXCLUDED.title, family=EXCLUDED.family,
                representative_item_id=EXCLUDED.representative_item_id,
                embedding=EXCLUDED.embedding, importance=EXCLUDED.importance,
                personal=EXCLUDED.personal, final_rank=EXCLUDED.final_rank,
                tier=EXCLUDED.tier, mention_count=EXCLUDED.mention_count
        """
        async with self._require_pool().connection() as conn:
            async with conn.cursor() as cur:
                for story in stories:
                    params = _rows.story_to_params(story)
                    params["embedding"] = _vec(story.embedding)
                    await cur.execute(story_sql, params)
                    await cur.execute(
                        "DELETE FROM story_items WHERE story_id = %s", (story.id,)
                    )
                    for item_id in story.item_ids:
                        await cur.execute(
                            "INSERT INTO story_items (story_id, item_id) VALUES (%s, %s) "
                            "ON CONFLICT DO NOTHING",
                            (story.id, item_id),
                        )
        return len(stories)

    async def delete_stories_for_date(self, date: str) -> int:
        """Delete all stories bucketed to the given LOCAL day (story_items cascade).

        Lets run_process REPLACE a day's stories instead of accumulating them, so a
        re-process always yields the canonical current set — a story dropped by the
        (possibly newly-fixed) curator can never linger from an earlier run.
        """
        tz = get_settings().timezone
        async with self._require_pool().connection() as conn:
            cur = await conn.execute(
                "DELETE FROM stories WHERE (created_at AT TIME ZONE %s)::date = %s::date",
                [tz, date],
            )
            return cur.rowcount

    async def get_stories_for_date(self, date: str) -> list[Story]:
        # created_at is stored UTC (timestamptz); bucket by the user's LOCAL day so
        # the daily digest matches _today_iso() even when the UTC date differs from
        # the local date (evening-local runs). Prevents a false "quiet day".
        tz = get_settings().timezone
        sql = (
            "SELECT * FROM stories "
            "WHERE (created_at AT TIME ZONE %s)::date = %s::date "
            "ORDER BY final_rank DESC"
        )
        rows = await self._fetch_dicts(sql, [tz, date])
        return await self._attach_items(rows)

    async def get_stories_by_ids(self, ids: list[str]) -> list[Story]:
        if not ids:
            return []
        rows = await self._fetch_dicts("SELECT * FROM stories WHERE id = ANY(%s)", [ids])
        return await self._attach_items(rows)

    async def _attach_items(self, rows: list[dict[str, Any]]) -> list[Story]:
        stories: list[Story] = []
        for row in rows:
            members = await self._fetch_dicts(
                "SELECT item_id FROM story_items WHERE story_id = %s", [row["id"]]
            )
            ids = [m["item_id"] for m in members]
            stories.append(_rows.row_to_story(row, ids))
        return stories

    # --------------------------------------------------------------- digests
    async def save_daily(self, digest: DailyDigest) -> None:
        await self._save_digest(digest, kind=DigestKind.DAILY, date=digest.date)

    async def save_weekly(self, digest: WeeklyDigest) -> None:
        await self._save_digest(digest, kind=DigestKind.WEEKLY, date=digest.week_of)

    async def _save_digest(
        self, digest: DailyDigest | WeeklyDigest, *, kind: DigestKind, date: str
    ) -> None:
        content = digest.model_dump(mode="json")
        quiet = bool(getattr(digest, "quiet_day", False) or getattr(digest, "quiet_week", False))
        sql = """
            INSERT INTO digests (id, kind, date, tier, quiet, story_ids, content,
                                 model, cost_usd, eval_scores, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                tier=EXCLUDED.tier, quiet=EXCLUDED.quiet, story_ids=EXCLUDED.story_ids,
                content=EXCLUDED.content, model=EXCLUDED.model, cost_usd=EXCLUDED.cost_usd,
                eval_scores=EXCLUDED.eval_scores
        """
        async with self._require_pool().connection() as conn:
            await conn.execute(
                sql,
                (
                    digest.id,
                    kind.value,
                    date,
                    digest.overall_tier.value,
                    quiet,
                    list(digest.story_ids),
                    json.dumps(content),
                    digest.model,
                    digest.cost_usd,
                    json.dumps(digest.eval_scores),
                    digest.created_at,
                ),
            )

    async def get_digest(self, digest_id: str) -> DailyDigest | WeeklyDigest | None:
        rows = await self._fetch_dicts(
            "SELECT kind, content FROM digests WHERE id = %s", [digest_id]
        )
        if not rows:
            return None
        row = rows[0]
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        return _rows.deserialize_digest(content, row["kind"])

    async def list_digests(
        self, *, kind: DigestKind | None = None, limit: int = 30
    ) -> list[dict]:
        sql = "SELECT id, kind, date, tier, quiet, content, created_at FROM digests"
        params: list[Any] = []
        if kind is not None:
            sql += " WHERE kind = %s"
            params.append(kind.value)
        sql += " ORDER BY date DESC, created_at DESC LIMIT %s"
        params.append(limit)
        rows = await self._fetch_dicts(sql, params)
        return [_rows.digest_summary_row(_load_content(r)) for r in rows]

    # -------------------------------------------------------------- feedback
    async def add_feedback(self, fb: Feedback) -> Feedback:
        sql = """
            INSERT INTO feedback ("user", target_id, target_kind, signal, value, text, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        async with self._require_pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    (
                        fb.user,
                        fb.target_id,
                        fb.target_kind.value,
                        fb.signal.value,
                        fb.value,
                        fb.text,
                        fb.created_at,
                    ),
                )
                row = await cur.fetchone()
        new_id = str(row[0]) if row else None
        return fb.model_copy(update={"id": new_id})

    async def get_feedback(
        self, *, signal: FeedbackSignal | None = None, since: datetime | None = None
    ) -> list[Feedback]:
        sql = "SELECT * FROM feedback WHERE 1=1"
        params: list[Any] = []
        if signal is not None:
            sql += " AND signal = %s"
            params.append(signal.value)
        if since is not None:
            sql += " AND created_at >= %s"
            params.append(since)
        sql += " ORDER BY created_at DESC"
        return [_rows.row_to_feedback(r) for r in await self._fetch_dicts(sql, params)]

    # --------------------------------------------------------------- sources
    async def upsert_sources(self, sources: list[Source]) -> int:
        if not sources:
            return 0
        sql = """
            INSERT INTO sources (name, family, url, authority, enabled, config)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                family=EXCLUDED.family, url=EXCLUDED.url, authority=EXCLUDED.authority,
                enabled=EXCLUDED.enabled, config=EXCLUDED.config
        """
        async with self._require_pool().connection() as conn:
            async with conn.cursor() as cur:
                for s in sources:
                    await cur.execute(
                        sql,
                        (s.name, s.family.value, s.url, s.authority, s.enabled,
                         json.dumps(s.config)),
                    )
        return len(sources)

    async def get_sources(self, *, enabled_only: bool = True) -> list[Source]:
        sql = "SELECT * FROM sources"
        if enabled_only:
            sql += " WHERE enabled = TRUE"
        sql += " ORDER BY name"
        return [_rows.row_to_source(r) for r in await self._fetch_dicts(sql, [])]

    # ------------------------------------------------------------------ eval
    async def save_eval_run(
        self, *, digest_id: str, judge_model: str, scores: dict, notes: str | None = None
    ) -> None:
        sql = """
            INSERT INTO eval_runs (digest_id, judge_model, scores, notes)
            VALUES (%s, %s, %s, %s)
        """
        async with self._require_pool().connection() as conn:
            await conn.execute(sql, (digest_id, judge_model, json.dumps(scores), notes))

    # ------------------------------------------------------------- app_state
    async def save_app_state(self, key: str, value: dict) -> None:
        """Upsert a singleton JSON value under `key` (interest vector / profile)."""
        sql = """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """
        async with self._require_pool().connection() as conn:
            await conn.execute(sql, (key, json.dumps(value)))

    async def get_app_state(self, key: str) -> dict | None:
        rows = await self._fetch_dicts("SELECT value FROM app_state WHERE key = %s", [key])
        if not rows:
            return None
        value = rows[0]["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return value if isinstance(value, dict) else None

    async def save_interest_vector(self, vector: list[float]) -> None:
        """Persist the nightly-recomputed interest vector (Loop 2)."""
        await self.save_app_state("interest_vector", {"vector": list(vector), "dim": len(vector)})

    async def get_interest_vector(self) -> list[float] | None:
        state = await self.get_app_state("interest_vector")
        if not state:
            return None
        vector = state.get("vector")
        return [float(v) for v in vector] if isinstance(vector, list) else None

    async def save_profile_override(self, profile: dict) -> None:
        """Persist the NL-steered profile override (Loop 3) so it survives restart."""
        await self.save_app_state("profile_override", dict(profile))

    async def get_profile_override(self) -> dict | None:
        return await self.get_app_state("profile_override")

    # -------------------------------------------------------- vector search
    async def similar_items(
        self, embedding: list[float], *, k: int = 20, since: datetime | None = None
    ) -> list[tuple[Item, float]]:
        """k nearest items by cosine distance; returns (item, similarity in [0,1])."""
        sql = (
            "SELECT *, 1 - (embedding <=> %s) AS similarity FROM items "
            "WHERE embedding IS NOT NULL"
        )
        params: list[Any] = [_vec(embedding)]
        if since is not None:
            sql += " AND published_at >= %s"
            params.append(since)
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params.extend([_vec(embedding), k])
        rows = await self._fetch_dicts(sql, params)
        out: list[tuple[Item, float]] = []
        for row in rows:
            sim = float(row.pop("similarity", 0.0))
            out.append((_rows.row_to_item(row), max(0.0, min(1.0, sim))))
        return out

    # ----------------------------------------------------------- low-level
    async def _fetch_dicts(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        async with self._require_pool().connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                return list(await cur.fetchall())


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def _vec(embedding: list[float] | None) -> Any:
    """Wrap a python list as a pgvector Vector when present (lazy import)."""
    if embedding is None:
        return None
    from pgvector import Vector

    return Vector(embedding)


def _jsonify(params: dict[str, Any]) -> dict[str, Any]:
    """Serialize jsonb-bound dict columns + wrap the embedding for pgvector."""
    out = dict(params)
    from psycopg.types.json import Jsonb

    out["metrics"] = Jsonb(out.get("metrics") or {})
    out["raw"] = Jsonb(out.get("raw") or {})
    out["embedding"] = _vec(out.get("embedding"))
    return out


def _load_content(row: dict[str, Any]) -> dict[str, Any]:
    content = row.get("content")
    if isinstance(content, str):
        row = dict(row)
        row["content"] = json.loads(content)
    return row


_REPO_SINGLETON: Repo | None = None


async def get_repo() -> Repo:
    """Return a process-wide connected Repo singleton (used by API + flows)."""
    global _REPO_SINGLETON
    if _REPO_SINGLETON is None:
        repo = Repo()
        await repo.connect()
        _REPO_SINGLETON = repo
    return _REPO_SINGLETON


__all__ = ["Repo", "get_repo"]
