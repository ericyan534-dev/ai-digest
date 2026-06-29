# ai-digest

> *A smol.ai for an audience of one — that also reads arXiv.*

A self-hosted, single-user, **LLM-powered personal AI-news engine**. It ingests
community + industry + **academia** sources, compresses them with a Gemini
pipeline into a **short daily digest** and a **long weekly "Week at a Glance"**
editorial, serves them in an interactive web app (+ email + Telegram), and
**learns from your 👍/👎 feedback**.

Full design rationale: [`AI_DIGEST_WIKI.md`](./AI_DIGEST_WIKI.md).

---

## Quickstart

Prereqs: Docker, Python 3.13, Node 20+.

```bash
# 0. configure secrets (already gitignored)
cp .env.example .env           # then set GEMINI_API_KEY=...

# 1. install deps (backend + frontend)
make install

# 2. start Postgres + pgvector
make up

# 3. apply the schema
make migrate

# 4. generate a digest
#    - offline/deterministic (no key, no network):
AIDIGEST_LLM_MOCK=1 make daily
#    - live (real gemini-3.5-flash):
make daily

# 5. run the backend API and the web app (separate terminals)
make api        # FastAPI on http://localhost:8000
make web        # Next.js on http://localhost:3000

# tests / quality
make test       # pytest (mock LLM, zero network)
make lint       # ruff + mypy
make smoke      # LIVE smoke test against real Gemini (needs GEMINI_API_KEY)
```

### Setting `GEMINI_API_KEY`

The key lives **only** in `.env` (gitignored — never commit it):

```
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-3.5-flash
GEMINI_EMBED_MODEL=gemini-embedding-001
EMBED_DIM=1536
```

Set `AIDIGEST_LLM_MOCK=1` to run the entire pipeline with a deterministic offline
mock LLM (no key, no network) — this is what tests and CI use.

---

## Architecture (six stages, one feedback loop)

```
SOURCES → INGEST → STORE → PROCESS → GENERATE → DELIVER
                                ▲           feedback (👍/👎) re-ranks tomorrow
```

| Stage | Package | What it does |
|---|---|---|
| Ingest | `aidigest.ingest` | One small adapter per source → normalized `Item`s. |
| Store | `aidigest.db` | Postgres + pgvector (`vector(1536)`, HNSW cosine). Repository pattern. |
| Process | `aidigest.process` | embed → dedup → cluster into stories → rank. |
| Personalize | `aidigest.personalize` | static profile vector (Loop 1) + feedback centroid (Loop 2) + NL steering (Loop 3). |
| Generate | `aidigest.generate` | importance-tier gate → daily map-reduce; weekly best-of-N + judge. |
| Deliver | `aidigest.deliver` + `frontend/` | markdown/HTML renderers, Resend email, Telegram, Next.js web app. |
| Eval | `aidigest.eval` | LLM-as-judge rubric (insight/accuracy/narrative/personal-fit/honesty). |
| Flows | `aidigest.flows` | end-to-end orchestration (`run_ingest/process/daily/weekly/nightly`). |

### The flexibility principle (the soul of the product)

The pipeline classifies each story into a tier —
**BREAKTHROUGH | NOTABLE | MINOR | QUIET_DAY** — and the generators honor it:
quiet days are called quiet ("Quiet day — nothing major shipped"), genuine
breakthroughs get full depth, everything else is a one-line trend note. No
manufactured importance.

### Personalization

Tailored to the user's subfields (Multi-Agent Systems; Efficient & Scalable NLP;
RL for NLP; LLMs & Foundation Models; Optimization) and venues (NeurIPS, ACL),
with a Karpathy/smol.ai/LeCun/Ng editorial voice. Encoded in
[`backend/profile.yaml`](./backend/profile.yaml).

---

## Repo layout

```
backend/
  aidigest/
    config.py          # pydantic-settings (get_settings)
    models.py          # the typed spine (Item, Story, DailyDigest, WeeklyDigest, …)
    db/                # schema.sql + repo.py (psycopg v3 + pgvector)
    llm/               # base Protocol, mock, gemini, factory
    ingest/            # base Adapter + one file per source + registry
    process/           # embed, dedup, cluster, rank, enrich
    personalize/       # profile, feedback
    generate/          # importance (tier gate), daily, weekly, prompts/
    deliver/           # render_md, email_resend, telegram_bot
    api/               # FastAPI app (main.py, schemas.py)
    eval/              # rubric.py, judge.py
    flows/             # pipeline.py
  profile.yaml         # the user's interests
  scripts/             # migrate / run_daily / run_weekly / smoke / ingest
  tests/
infra/                 # docker-compose.yml (Postgres 16 + pgvector)
frontend/              # Next.js (App Router, TS, Tailwind) — "Hybrid Editorial" UI
INTERFACES.md          # module/function contract (binding for implementers)
API_CONTRACT.md        # REST contract (frontend ↔ backend)
ACCEPTANCE.md          # the quality gate + editorial rubric + UI design tokens
```

See [`INTERFACES.md`](./INTERFACES.md), [`API_CONTRACT.md`](./API_CONTRACT.md),
and [`ACCEPTANCE.md`](./ACCEPTANCE.md) for the exact contracts.
