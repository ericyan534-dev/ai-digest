# INTERFACES.md — the integration contract

This is the binding contract for all 7 parallel implementers. **Match these
module paths and signatures EXACTLY** — names, argument names, types, returns.
Integration drift = build failure.

Foundation (already written, do not change): `aidigest.config`, `aidigest.models`,
`aidigest.llm.base`, `aidigest.llm.mock`, `aidigest.llm.factory`, `aidigest.ingest.base`,
`aidigest.eval.rubric`, `aidigest/db/schema.sql`, `backend/profile.yaml`.

Conventions:
- All code is `async` where it does I/O. Use `asyncio`.
- All LLM calls go through `aidigest.llm.factory.get_llm()` -> `LLMClient`.
- All outbound HTTP uses `aidigest.ingest.base.make_async_client()` + `with_retry()`.
- Never mutate Pydantic models; use `.model_copy(update=...)`.
- Imports are absolute: `from aidigest.models import Item`.
- Type everything; code must pass `ruff` and `mypy`.

The foundation models/enums (import, never redefine):
`Family`, `ImportanceTier`, `FeedbackSignal`, `FeedbackTargetKind`, `DigestKind`,
`Source`, `Item`, `Story`, `StorySummary`, `DigestSection`, `DailyDigest`,
`WeeklyShortlistEntry`, `WeeklyDigest`, `Feedback`, `content_hash`, `slugify`.

---

## 1. Ingestion — `aidigest.ingest.*`  (Implementer: INGEST)

Each adapter is one small file satisfying the `Adapter` Protocol in
`aidigest/ingest/base.py`:

```python
class Adapter(Protocol):
    name: str
    family: Family
    async def fetch(self, since: datetime) -> list[Item]: ...
```

Adapters to implement (one file each), each exporting a module-level instance
named `ADAPTER` plus the class:

| Module | name | family |
|---|---|---|
| `aidigest/ingest/hn.py` | `"hn"` | `Family.COMMUNITY` |
| `aidigest/ingest/reddit.py` | `"reddit"` | `Family.COMMUNITY` |
| `aidigest/ingest/arxiv.py` | `"arxiv"` | `Family.ACADEMIA` |
| `aidigest/ingest/openreview.py` | `"openreview"` | `Family.ACADEMIA` |
| `aidigest/ingest/semantic_scholar.py` | `"semantic_scholar"` | `Family.ACADEMIA` |
| `aidigest/ingest/hf_papers.py` | `"hf_papers"` | `Family.ACADEMIA` |
| `aidigest/ingest/rss.py` | `"rss:<name>"` | `Family.INDUSTRY` / `Family.META` |
| `aidigest/ingest/smolai.py` | `"smol.ai"` | `Family.META` |

`rss.py` is parameterized:

```python
class RSSAdapter:
    name: str
    family: Family
    def __init__(self, *, name: str, url: str, family: Family) -> None: ...
    async def fetch(self, since: datetime) -> list[Item]: ...
```

Registry — `aidigest/ingest/registry.py`:

```python
def all_adapters() -> list[Adapter]:
    """Return every enabled adapter instance (built from profile.yaml + RSS feed list)."""

def get_adapter(name: str) -> Adapter | None: ...

async def ingest_all(since: datetime, *, adapters: list[Adapter] | None = None) -> list[Item]:
    """Run all adapters concurrently (asyncio.gather, exceptions isolated per adapter),
    return the combined, de-duplicated-by-id list of Items."""
```

Adapters MUST: build Items with `Item.create(...)`; use `make_async_client()` +
`with_retry()`; never raise on a single bad record (skip & continue); never
require a paid API.

---

## 2. Database — `aidigest.db.repo`  (Implementer: DB)

`aidigest/db/repo.py` — repository pattern over psycopg v3 (async). The DSN comes
from `get_settings().database_url`; register the pgvector type adapter on connect.

```python
class Repo:
    def __init__(self, dsn: str | None = None) -> None: ...
    async def connect(self) -> None: ...          # open async pool/conn, register vector
    async def close(self) -> None: ...
    async def init_schema(self) -> None:          # apply db/schema.sql (idempotent)
        ...

    # --- items ---
    async def upsert_items(self, items: list[Item]) -> int:
        """Insert/update by id; returns count written. Stores embedding as vector(1536)."""
    async def get_items_since(self, since: datetime, *, family: Family | None = None) -> list[Item]: ...
    async def get_items_by_ids(self, ids: list[str]) -> list[Item]: ...
    async def get_items_without_embedding(self, limit: int = 500) -> list[Item]: ...
    async def set_item_embedding(self, item_id: str, embedding: list[float]) -> None: ...

    # --- stories ---
    async def upsert_stories(self, stories: list[Story]) -> int:
        """Upsert stories + replace their story_items membership rows."""
    async def get_stories_for_date(self, date: str) -> list[Story]:
        """Stories whose created_at falls on the given ISO date (UTC)."""
    async def get_stories_by_ids(self, ids: list[str]) -> list[Story]: ...

    # --- digests ---
    async def save_daily(self, digest: DailyDigest) -> None:
        """Serialize into `digests` (kind='daily', content=jsonb, tier, quiet, story_ids)."""
    async def save_weekly(self, digest: WeeklyDigest) -> None: ...
    async def get_digest(self, digest_id: str) -> DailyDigest | WeeklyDigest | None:
        """Deserialize content jsonb back into the right model by `kind`."""
    async def list_digests(self, *, kind: DigestKind | None = None, limit: int = 30) -> list[dict]:
        """Lightweight rows for the archive: {id, kind, date, tier, quiet, tldr|title, created_at}."""

    # --- feedback ---
    async def add_feedback(self, fb: Feedback) -> Feedback:
        """Insert; return the stored Feedback with its assigned id."""
    async def get_feedback(self, *, signal: FeedbackSignal | None = None,
                           since: datetime | None = None) -> list[Feedback]: ...

    # --- sources ---
    async def upsert_sources(self, sources: list[Source]) -> int: ...
    async def get_sources(self, *, enabled_only: bool = True) -> list[Source]: ...

    # --- eval ---
    async def save_eval_run(self, *, digest_id: str, judge_model: str,
                            scores: dict, notes: str | None = None) -> None: ...

    # --- vector search (cosine) ---
    async def similar_items(self, embedding: list[float], *, k: int = 20,
                            since: datetime | None = None) -> list[tuple[Item, float]]:
        """k nearest items by cosine distance; returns (item, similarity) pairs, similarity in [0,1]."""
```

A module-level `async def get_repo() -> Repo` helper that returns a connected
singleton is expected by the API and flows.

---

## 3. Processing — `aidigest.process.*`  (Implementer: PROCESS)

`aidigest/process/embed.py`:
```python
async def embed_items(items: list[Item], *, llm: LLMClient | None = None) -> list[Item]:
    """Embed (title + lead of raw_text) for items missing embeddings; return NEW items
    with .embedding set (len == settings.embed_dim). Uses get_llm() when llm is None."""

def embedding_text(item: Item) -> str:
    """The canonical text fed to the embedder: f'{item.title}\n{item.raw_text[:N]}'."""
```

`aidigest/process/dedup.py`:
```python
def dedup(items: list[Item], *, threshold: float = 0.90) -> list[list[Item]]:
    """Group near-duplicate items by embedding cosine similarity (>= threshold).
    Returns clusters (each a list of Items); the highest-authority/most-engaged item
    is index 0 of each cluster (the representative). Items lacking embeddings form
    singleton clusters."""
```

`aidigest/process/cluster.py`:
```python
def cluster_into_stories(items: list[Item], *, threshold: float = 0.75) -> list[Story]:
    """Looser cosine pass grouping deduped items into Story objects. Each Story gets:
    id (slug+hash), title (representative item's title), family (modal family),
    item_ids, representative_item_id, centroid embedding, mention_count = #items.
    importance/personal/final_rank/tier are left at defaults (filled by rank/importance)."""

def story_id(title: str, item_ids: list[str]) -> str:
    """Stable story id = slugify(title) + '-' + short hash(sorted item_ids)."""
```

`aidigest/process/rank.py`:
```python
def score_stories(stories: list[Story], *, interest_vector: list[float] | None,
                  profile: dict, feedback_boost: dict[str, float] | None = None) -> list[Story]:
    """Compute importance, personal (cosine to interest_vector), and final_rank per
    profile['ranking'] weights (alpha/beta/gamma + importance_weights). Returns NEW
    Story copies sorted by final_rank desc. feedback_boost maps story_id -> delta."""

def importance_score(story: Story, *, profile: dict) -> float: ...
def personal_score(story: Story, interest_vector: list[float] | None) -> float: ...
def cosine(a: list[float], b: list[float]) -> float: ...
```

`aidigest/process/enrich.py`:
```python
async def enrich_stories(stories: list[Story], items_by_id: dict[str, Item], *,
                         llm: LLMClient | None = None) -> list[Story]:
    """Optional LLM titling/labeling pass (cheap). Returns NEW stories with improved
    titles where useful. Must be a no-op-safe pass (never drops stories)."""
```

---

## 4. Real Gemini client — `aidigest.llm.gemini`  (Implementer: LLM)

`aidigest/llm/gemini.py` — implements `LLMClient` (structurally). Read the HARD
REQUIREMENTS in `aidigest/llm/base.py` docstring and obey them.

```python
class GeminiClient:
    model: str
    embed_model: str
    embed_dim: int
    def __init__(self, *, settings: Settings | None = None) -> None: ...
    async def generate(self, prompt, *, max_output_tokens=8192, temperature=0.7,
                       json_schema=None) -> str: ...
    async def generate_detailed(self, prompt, *, max_output_tokens=8192,
                                temperature=0.7, json_schema=None) -> GenerationResult: ...
    async def embed(self, texts: list[str], *, task_type="RETRIEVAL_DOCUMENT") -> list[list[float]]: ...
    async def judge(self, *, candidates: list[str], rubric: dict, context: str = "") -> dict: ...
```

Endpoints (settings.gemini_base_url + key from settings.gemini_api_key):
- generate: `POST /models/{model}:generateContent?key=...`
- embed:    `POST /models/{embed_model}:embedContent?key=...`
  body: `{"content":{"parts":[{"text":...}]},"outputDimensionality":1536,"taskType":...}`

MUST: collect only non-thought text parts; handle `finishReason=="MAX_TOKENS"`
without raising; L2-normalize embeddings to length 1536; retry >=5x via
`with_retry`. The mock and the real client are interchangeable.

---

## 5. Personalization — `aidigest.personalize.*`  (Implementer: PERSONALIZE)

`aidigest/personalize/profile.py`:
```python
def load_profile(path: str | None = None) -> dict:
    """Load backend/profile.yaml (default) into a dict. Validates required keys."""

async def build_interest_vector(profile: dict, *, llm: LLMClient | None = None) -> list[float]:
    """Embed the profile's subfields/voices/venues into ONE L2-normalized interest
    vector (len == embed_dim). Uses task_type='RETRIEVAL_QUERY'."""

def profile_text(profile: dict) -> str:
    """The text blob embedded to form the interest vector."""
```

`aidigest/personalize/feedback.py`:
```python
async def recompute_interest_vector(repo: "Repo", profile: dict, *,
                                    llm: LLMClient | None = None,
                                    half_life_days: float = 14.0) -> list[float]:
    """Loop 2: decayed weighted centroid of embeddings of 👍'd stories MINUS 👎'd,
    blended with the static profile vector. Returns an L2-normalized vector."""

def feedback_boosts(feedback: list[Feedback], *, half_life_days: float = 14.0) -> dict[str, float]:
    """Map target_id -> ranking delta from up/down/click/dwell signals (time-decayed)."""

async def apply_nl_instruction(instruction: str, profile: dict, *,
                               llm: LLMClient | None = None) -> dict:
    """Loop 3: convert a natural-language steering instruction into a NEW profile dict
    (adjusted weights / added mutes). Returns the updated profile (does not write to disk)."""
```

---

## 6. Generation — `aidigest.generate.*`  (Implementer: GENERATE)

`aidigest/generate/importance.py` — the flexibility-principle gate (CRITICAL):
```python
def classify_tier(story: Story, *, profile: dict, day_top_score: float) -> ImportanceTier:
    """Classify a story into BREAKTHROUGH | NOTABLE | MINOR | QUIET_DAY using
    profile['tiers'] thresholds against story.final_rank, relative to day_top_score."""

def classify_day(stories: list[Story], *, profile: dict) -> tuple[list[Story], ImportanceTier, bool]:
    """Return (stories-with-tier-set, overall_tier, quiet_day). quiet_day is True when
    the top story's score < profile['tiers']['quiet_day_top_score']. overall_tier is the
    max tier present. Returns NEW Story copies with .tier set."""
```

`aidigest/generate/daily.py`:
```python
async def generate_daily(stories: list[Story], items_by_id: dict[str, Item], *,
                         profile: dict, date: str, llm: LLMClient | None = None,
                         max_items: int | None = None) -> DailyDigest:
    """Map-reduce daily. Map: per top story -> StorySummary honoring its tier (BREAKTHROUGH
    => full depth; MINOR => one line). Reduce: assemble DigestSections grouped by family +
    one-line tldr. Honest quiet-day handling: if quiet, tldr says so and sections stay short.
    max_items defaults to settings.daily_max_items. id = f'daily-{date}'."""

async def map_story(story: Story, items_by_id: dict[str, Item], *, profile: dict,
                    llm: LLMClient) -> StorySummary: ...
```

`aidigest/generate/weekly.py`:
```python
async def generate_weekly(stories: list[Story], items_by_id: dict[str, Item], *,
                          profile: dict, week_of: str, feedback: list[Feedback] | None = None,
                          llm: LLMClient | None = None, n_candidates: int = 3) -> WeeklyDigest:
    """Best-of-N: generate n_candidates editorials with different leads, judge via
    eval.judge.judge_candidates (rubric), pick winner, polish. Includes shortlist
    ('What I'd actually read') + on_my_radar (academia preview). Honest quiet-week handling.
    id = f'weekly-{week_of-as-ISO-week}'."""
```

Prompt templates live in `aidigest/generate/prompts/` (plain `.md`/`.txt`,
loaded by the generators). The daily/weekly prompts MUST instruct the model to
honor the active `ImportanceTier`.

---

## 7. Delivery — `aidigest.deliver.*`  (Implementer: DELIVER)

`aidigest/deliver/render_md.py`:
```python
def render_daily_md(digest: DailyDigest) -> str: ...
def render_weekly_md(digest: WeeklyDigest) -> str: ...
def render_daily_html(digest: DailyDigest) -> str: ...     # for email
def render_weekly_html(digest: WeeklyDigest) -> str: ...
```

`aidigest/deliver/email_resend.py`:
```python
async def send_email(*, subject: str, html: str, to: str | None = None,
                     settings: Settings | None = None) -> bool:
    """POST to Resend HTTP API with retry. No-op returning False when not email_enabled.
    Never raises on disabled config."""
```

`aidigest/deliver/telegram_bot.py`:
```python
async def send_message(text: str, *, settings: Settings | None = None) -> bool:
    """Send a Markdown message via Telegram Bot API (raw HTTP + retry). No-op/False
    when not telegram_enabled."""

async def send_daily(digest: DailyDigest, *, settings: Settings | None = None) -> bool: ...
```

---

## 8. API — `aidigest.api.main`  (Implementer: API)

`aidigest/api/main.py` exposes `app = FastAPI(...)`. Routes per `API_CONTRACT.md`:

```
GET  /api/health                       -> {"status":"ok", ...}
GET  /api/digests?kind=&limit=         -> list of digest summary rows
GET  /api/digest/{id}                  -> full DailyDigest|WeeklyDigest (serialized)
GET  /api/stories?date=YYYY-MM-DD      -> list[Story]
POST /api/feedback {target_id,target_kind,signal,value,text?} -> {"ok":true,"id":...}
POST /api/tune {instruction}           -> {"ok":true,"profile":{...}}
```

CORS enabled for the Next.js dev origin. Use `Repo` via `get_repo()`; validate
request bodies with Pydantic request models defined in `api/schemas.py`. Map
domain models to JSON via `model_dump(mode="json")`.

---

## 9. Flows — `aidigest.flows.pipeline`  (Implementer: FLOWS/INTEGRATION)

`aidigest/flows/pipeline.py` — orchestrates the stages end-to-end (plain async
functions; Prefect optional later):

```python
async def run_ingest(*, since: datetime | None = None) -> int:
    """Ingest -> upsert_items. Returns #items written."""

async def run_process(*, since: datetime | None = None) -> int:
    """embed -> dedup -> cluster -> rank -> enrich -> upsert_stories. Returns #stories."""

async def run_daily(*, date: str | None = None, deliver: bool = False) -> DailyDigest:
    """Full daily: ensure stories for date, classify tiers, generate_daily, save_daily,
    optionally deliver. date defaults to today (settings.timezone)."""

async def run_weekly(*, week_of: str | None = None, deliver: bool = False) -> WeeklyDigest: ...

async def run_nightly() -> None:
    """recompute interest vector (Loop 2) + eval run on latest daily."""
```

`backend/scripts/` thin CLI entrypoints (argparse) call these:
`run_daily.py`, `run_weekly.py`, `migrate.py`, `smoke.py`, `ingest.py`.
`migrate.py` connects and calls `repo.init_schema()`. `smoke.py` runs a LIVE
(non-mock) daily over a tiny fixed item set and prints the digest.

---

## 10. Eval — `aidigest.eval.*`  (Implementer: GENERATE or EVAL)

`aidigest/eval/rubric.py` (DONE) provides `rubric()`, `weighted_total(scores)`,
`criteria_names()`.

`aidigest/eval/judge.py`:
```python
async def judge_candidates(candidates: list[str], *, context: str = "",
                           llm: LLMClient | None = None) -> dict:
    """Thin wrapper over llm.judge(candidates=..., rubric=rubric(), context=...).
    Returns {'winner': int, 'scores': [...], 'rationale': str}."""

async def grade_digest(digest_markdown: str, *, quiet_expected: bool,
                       llm: LLMClient | None = None) -> dict:
    """Grade one digest against the rubric; enforce the quiet-day honesty gate
    (cap score per rubric.QUIET_DAY_CHECK when violated). Returns
    {'scores': {<criterion>: float}, 'total': float, 'quiet_ok': bool, 'notes': str}."""
```

---

## Frontend  (Implementer: FRONTEND)

Next.js (App Router, TS, Tailwind) in `frontend/`. Codes against `API_CONTRACT.md`
only. Design tokens in `ACCEPTANCE.md` (Hybrid Editorial). Backend base URL via
`NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`). Views: Today, Week at a
Glance, Archive, Story detail. Must POST feedback + tune.
