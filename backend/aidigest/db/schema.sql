-- ai-digest schema (PostgreSQL 16 + pgvector). Idempotent: safe to re-run.
-- Embeddings are vector(1536) (Gemini Matryoshka @ outputDimensionality=1536,
-- L2-normalized) so they fit under pgvector's 2000-dim HNSW index limit.

CREATE EXTENSION IF NOT EXISTS vector;

-- --------------------------------------------------------------------------
-- sources: the ingestion registry
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    name        TEXT PRIMARY KEY,
    family      TEXT NOT NULL CHECK (family IN ('academia','industry','community','meta')),
    url         TEXT,
    authority   REAL NOT NULL DEFAULT 0.5,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------------------
-- items: normalized ingestion units (content-hash id)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,                -- sha256(canonical_url or normalized_text)
    source        TEXT NOT NULL,
    family        TEXT NOT NULL CHECK (family IN ('academia','industry','community','meta')),
    url           TEXT,
    title         TEXT NOT NULL,
    author        TEXT,
    published_at  TIMESTAMPTZ NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_text      TEXT NOT NULL DEFAULT '',
    embedding     vector(1536),
    metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_items_published_at ON items (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_family       ON items (family);
CREATE INDEX IF NOT EXISTS idx_items_source       ON items (source);

-- HNSW index over item embeddings for fast cosine similarity (dedup / cluster / recsys).
CREATE INDEX IF NOT EXISTS idx_items_embedding_hnsw
    ON items USING hnsw (embedding vector_cosine_ops);

-- --------------------------------------------------------------------------
-- stories: clusters of deduped items (the unit the digest talks about)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stories (
    id                     TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,
    family                 TEXT NOT NULL CHECK (family IN ('academia','industry','community','meta')),
    representative_item_id TEXT REFERENCES items (id) ON DELETE SET NULL,
    embedding              vector(1536),
    importance             REAL NOT NULL DEFAULT 0.0,
    personal               REAL NOT NULL DEFAULT 0.0,
    final_rank             REAL NOT NULL DEFAULT 0.0,
    tier                   TEXT NOT NULL DEFAULT 'minor'
                              CHECK (tier IN ('breakthrough','notable','minor','quiet_day')),
    mention_count          INTEGER NOT NULL DEFAULT 1,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stories_created_at ON stories (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stories_final_rank ON stories (final_rank DESC);
CREATE INDEX IF NOT EXISTS idx_stories_embedding_hnsw
    ON stories USING hnsw (embedding vector_cosine_ops);

-- --------------------------------------------------------------------------
-- story_items: many-to-many membership (story <-> items)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS story_items (
    story_id  TEXT NOT NULL REFERENCES stories (id) ON DELETE CASCADE,
    item_id   TEXT NOT NULL REFERENCES items (id)   ON DELETE CASCADE,
    PRIMARY KEY (story_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_story_items_item ON story_items (item_id);

-- --------------------------------------------------------------------------
-- digests: rendered daily/weekly outputs (content stored as JSONB)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS digests (
    id           TEXT PRIMARY KEY,                 -- "daily-2026-06-21" / "weekly-2026-W25"
    kind         TEXT NOT NULL CHECK (kind IN ('daily','weekly')),
    date         DATE NOT NULL,                    -- digest date / week-start
    tier         TEXT NOT NULL DEFAULT 'minor'
                    CHECK (tier IN ('breakthrough','notable','minor','quiet_day')),
    quiet        BOOLEAN NOT NULL DEFAULT FALSE,   -- quiet-day / quiet-week flag
    story_ids    TEXT[] NOT NULL DEFAULT '{}',
    content      JSONB NOT NULL,                   -- serialized DailyDigest / WeeklyDigest
    model        TEXT NOT NULL DEFAULT '',
    cost_usd     REAL NOT NULL DEFAULT 0.0,
    eval_scores  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_digests_kind_date ON digests (kind, date DESC);

-- --------------------------------------------------------------------------
-- feedback: 👍/👎, clicks, dwell, NL steering instructions
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "user"       TEXT NOT NULL DEFAULT 'me',
    target_id    TEXT NOT NULL,
    target_kind  TEXT NOT NULL CHECK (target_kind IN ('item','story','digest_section','digest')),
    signal       TEXT NOT NULL CHECK (signal IN ('up','down','click','dwell','nl_instruction')),
    value        REAL NOT NULL DEFAULT 1.0,
    text         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_target  ON feedback (target_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback (created_at DESC);

-- --------------------------------------------------------------------------
-- eval_runs: LLM-judge scores + did-I-miss-anything recall checks
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eval_runs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    digest_id   TEXT REFERENCES digests (id) ON DELETE CASCADE,
    judge_model TEXT NOT NULL DEFAULT '',
    scores      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {insight, accuracy, narrative, personal_fit, honesty}
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_digest ON eval_runs (digest_id);

-- --------------------------------------------------------------------------
-- app_state: singleton key/value JSON store for learned/steered state.
-- Holds the nightly-recomputed interest vector (Loop 2) and the NL-steered
-- profile override (Loop 3) so both survive a process restart.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_state (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
