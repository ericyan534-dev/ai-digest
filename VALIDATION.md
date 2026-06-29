# ai-digest — Validation Report

Date: 2026-06-22 · Build: contracts-first workflow (foundation + 7 modules + 4 reviewers) → main-thread validation & fixes.

## Acceptance gate (ACCEPTANCE.md) — results

| Gate | What | Status | Evidence |
|---|---|---|---|
| a | Postgres + pgvector starts | ✅ PASS | Validated against a real **PostgreSQL 16 + pgvector 0.8.3** instance. In this sandbox Docker had no daemon, so I stood up the *same* engine via conda to validate the true path; your normal workflow is `make up` (Docker, `pgvector/pgvector:pg16`). |
| b | Migrations apply | ✅ PASS | `repo.init_schema()` applied `db/schema.sql` cleanly on the live DB (idempotent). |
| c | Pipeline → valid DailyDigest + WeeklyDigest | ✅ PASS | Mock-mode unit/integration tests for daily+weekly; **live `run_daily` against real Postgres** (upsert → embed → dedup → cluster → rank → classify → generate → save → readback). |
| d | pytest passes incl. flexibility logic | ✅ PASS | **262 passed** in 0.56s (`AIDIGEST_LLM_MOCK=1`). Tier/quiet-day logic covered + a new breakthrough-reachability test. |
| e | FastAPI serves all endpoints | ✅ PASS | `test_api.py` exercises every API_CONTRACT route via TestClient + the in-memory repo (feedback round-trip incl.). |
| f | Frontend builds & renders | ✅ PASS | `next build` clean — all 6 routes (`/`, `/week`, `/archive`, `/digest/[id]`, `/story/[id]`, `/_not-found`). |
| g | LIVE gemini-3.5-flash smoke | ✅ PASS | Real end-to-end generation: 4 `embedContent` + 3 `generateContent` calls (all 200) → valid `DailyDigest`, terse/dense voice, subfield-tagged "why it matters". |
| h | No hardcoded secrets | ✅ PASS | Source scan: **0** key occurrences. Key loaded only from gitignored `.env`; now sent via `x-goog-api-key` **header** so it never appears in request logs either. |
| i | ruff + mypy clean | ✅ PASS | `ruff` all checks passed; `mypy` success, 58 source files, 0 issues. |

## Issues found & fixed during validation

**From the adversarial review (CRITICAL/HIGH):**
1. **CRITICAL** — `requirements.txt` missing runtime deps `pgvector` + `psycopg-pool` → `repo.py` would `ModuleNotFoundError` on first DB use. Fixed: `psycopg[binary,pool]==3.2.3` + `pgvector==0.4.2`.
2. **HIGH** — retry wrapper didn't cover `httpx.HTTPStatusError`, so transient **429/5xx** weren't retried. Fixed: retry on 429/500/502/503/504 (never on 4xx auth/bad-request).
3. **HIGH** — the "cannot miss a breakthrough" guarantee was mathematically unreachable (`final_rank` couldn't hit 0.85) and untested. Fixed: reachable threshold (0.72) + an **embedding-independent absolute-importance override** (0.78) so a high cross-source story is BREAKTHROUGH even with no personalization signal (cold start / mock) + a new reachability test that fails loudly on drift.

**Found by live validation (not in the static review):**
4. **Correctness bug — timezone day-bucketing.** `stories.created_at` is stored UTC but the daily queried by the *local* date, so for the ~7 h/day where UTC and local dates differ the digest silently dropped all stories and read a **false "quiet day"** (the inverse of "never miss a story"). Fixed: `get_stories_for_date` now buckets by `(created_at AT TIME ZONE <tz>)::date`; verified on the live DB (`get_stories_for_date('2026-06-22')` → 2/2 stories). FakeRepo mirrored.
5. **Security/hygiene** — the API key was passed as a `?key=` URL query param and thus logged by httpx at INFO. Fixed: moved to the `x-goog-api-key` header (0 key occurrences in logs, confirmed).
6. **Robustness** — `_vec.cosine_matrix` emitted spurious NumPy matmul RuntimeWarnings and could propagate NaN/inf into similarity scores. Fixed: sanitize inputs, guard the matmul with `np.errstate`, clip output to [-1, 1].
7. **Test speed** — suite took 11 min due to real backoff sleeps in failure-path tests. Fixed: backoff is instant in mock mode → **0.5s** suite.

## Known tuning items (data-calibration, not bugs)

- **Clustering threshold** (`cluster` cosine ~0.75): on the 3-item live smoke it merged a linear-attention paper with the DeepSeek post. Real embeddings of "AI research" are close; tune the threshold on real daily volume.
- **Tier calibration**: breakthrough/notable cutoffs are first-pass; they should be tuned against a few weeks of real ranked output + your 👍/👎.
- **Email inline feedback link**: the web app + Telegram use `POST /api/feedback`; email inline 👍/👎 links would want a `GET /api/feedback/click` shim (email self-disables until `RESEND_API_KEY` is set, so this is non-blocking). One small route to add when you enable email.
- **Direct X/Twitter ingestion**: v1 relies on aggregators (smol.ai/HN/Reddit) per design; add a paid X source in v2.

## How to run it

```bash
cp .env.example .env            # then put your GEMINI_API_KEY in .env
make up                         # Postgres 16 + pgvector (Docker)
make migrate                    # apply schema
make test                       # 275 tests, mock mode, ~1s
make smoke                      # LIVE gemini-3.5-flash end-to-end (needs key)
make eval                       # LIVE golden-set eval gate (flexibility + editorial floor)
make daily                      # generate today's digest
make wiki                       # export digests as linked Obsidian notes
make api                        # FastAPI on :8000
make web                        # Next.js on :3000
```

---

## Follow-up: clustering tuning · wiki export · eval gate (2026-06-25)

**1. Clustering, tuned on real data.** Measured on live ingested+embedded items, AI-news
embeddings are highly compressed (unrelated items ~0.71 median cosine, ≤0.86), so the original
**single-link @ 0.75 chained 51 unrelated items into one "story."** Switched to **complete-link**
(a story's members are all mutually ≥ threshold — no chaining), collapsed to a single pass that
**preserves cross-source `mention_count`** (the old dedup→representatives→cluster step silently
discarded it — undermining "breakthrough = many sources"), and tuned the threshold to **0.86**
(`profile.yaml processing.cluster_threshold`). Live smoke now yields 3 distinct stories where the
DeepSeek post and a linear-attention paper previously merged. Added an anti-chaining regression test.

**2. Karpathy-wiki Markdown export.** `deliver/wiki_export.py` renders digests to Obsidian-style
linked notes — `daily/<date>.md`, `weekly/<id>.md`, and `stories/<slug>.md` with YAML frontmatter,
`[[wikilinks]]`, `#subfield` tags, and backlinks, so issues compose into a browsable knowledge graph.
Set `AIDIGEST_WIKI_DIR` (or `make wiki`); auto-exported during delivery when configured.

**3. Golden-set eval gate + Langfuse.** A deterministic mock gate (`tests/test_eval_gate.py`, in
`make test`) plus a **live, score-floored gate** (`make eval`) assert the flexibility principle
end-to-end: quiet set → honestly quiet; busy set → breakthrough surfaces + editorial total ≥ floor.
`obs/langfuse.py` adds optional, import-guarded Langfuse tracing (no-op unless configured; never a
hard dependency).

**Bug the eval gate caught (and we fixed).** With real embeddings the quiet-day gate — based on
personal-inflated `final_rank` — never fired (everything AI is ~0.6–0.7 cosine to an AI interest
vector), so the system would rarely admit a quiet day; and `importance_score` double-counted academia
authority as "citation," ranking a routine ablation *above* the DeepSeek breakthrough. Fix: the Story
now carries **real engagement + citation** from its member items; `importance_score` leads with those
objective attention signals (authority/recency are gentle multipliers, not additive baselines); and
the **quiet-day gate now uses objective importance** (`tiers.quiet_day_min_importance`). The live eval
gate now passes honestly: `busy_day` → breakthrough (4.55), `quiet_day` → quiet (3.7).

---

## Phases A–C: from validated engine to operated app (2026-06-25)

Status after this pass: **312 tests pass** (mock, ~0.6s), ruff + mypy clean (66 source files),
`next build` clean (incl. 2 new proxy routes), all workflow YAML / fly TOML / promptfoo YAML parse.
Everything not requiring external credentials is built and tested; live switches (deploy, email/Telegram
send, Reddit/S2 keys) are wired and gated, pending the operator's secrets.

**Phase A — runs itself + reaches me.**
- `.github/workflows/digest.yml` — scheduled cron (ingest 3h / daily 14:00 UTC / weekly Sun / nightly),
  job-level secrets, `--deliver` on daily+weekly; `ci.yml` (ruff+mypy+pytest, next build).
- `GET /api/feedback/click` — email 👍/👎 shim (HMAC-verified when `AIDIGEST_LINK_SECRET` set), returns a
  confirmation page. `POST /api/telegram/webhook` — decodes `fb:<signal>:<kind>:<id>`, records feedback,
  acks the button. **Email + Telegram feedback loops are now CLOSED.**
- Weekly email `_markdown_body_to_html` now renders inline **bold**/*italic*/`code`/links/lists (no more
  raw asterisks). Daily email links use `AIDIGEST_PUBLIC_BASE_URL` + signing.
- `backend/Dockerfile`, `frontend/Dockerfile`, `backend/fly.toml`, `frontend/fly.toml` (configs in app
  dirs so build context matches), `infra/DEPLOY.md` runbook.

**Phase B — feed it real data.**
- Reddit app-only **OAuth** (`oauth.reddit.com`) with public-JSON fallback; HF papers switched to the
  **JSON API** (`/api/daily_papers`) with RSS fallback (fixes the 401/404); Semantic Scholar
  **citation-velocity enrichment now wired into `run_process`** (academia items, persisted) + `S2_API_KEY`
  config; **Jina web-reader** fallback for thin lab-blog RSS bodies (offline/mock-safe).

**Phase C — close the learning + safety loops.**
- `app_state` table + repo methods: the nightly-recomputed **interest vector is persisted** and read by
  the next day's ranking (Loop 2 closed across runs); the **NL-steered profile override is persisted**
  and re-applied at API startup + in the pipeline's effective profile (Loop 3 survives restart).
- `eval/recall.py` + `make recall` — the "did-I-miss-anything" omission eval (coverage metric + LLM
  omission pass + gate). `eval/promptfoo.yaml` + provider + `make promptfoo` — A/B the judge prompt.
- API security: optional `X-API-Key` gate + per-IP rate limit on mutating endpoints; signed email links;
  Telegram webhook secret. Frontend `/app/api/*` proxy routes keep the key server-side.
- Weekly best-of-N now uses an **independent judge client** (`get_judge_llm`, `JUDGE_MODEL`).

New `make` targets: `ingest`, `nightly`, `recall`, `promptfoo`. New tests: `test_security`,
`test_telegram_webhook`, `test_recall`, `test_promptfoo_provider`, `test_render_markdown`,
`test_persistence`, `test_ingest_reddit_oauth`, `test_reader` (+ updated HF tests).

**Not verifiable in this sandbox (needs operator secrets/accounts):** live Resend + Telegram send,
Reddit OAuth against live Reddit, S2/Jina live calls, real-Postgres `app_state` round-trip (apply via the
idempotent `make migrate`), and the actual Fly deploy + GitHub Actions run (repo must be pushed first).

---

## Tier-threshold tuning on real data (2026-06-25)

Built `scripts/tune_tiers.py` (`make tune`) — live-ingests real items, embeds with real Gemini,
clusters → ranks → classifies, and reports the actual `importance` / `final_rank` distributions plus the
tier histogram. Ran it on real windows (live Gemini, 60h and 96h).

**Findings (real data):**
- **Ingestion was degraded by stale feed URLs.** Three lab/curator RSS endpoints 404'd. Probed live and
  fixed `ingest/feeds.py`: `openai` → `/news/rss.xml`, `mistral` → `mistral.ai/rss.xml`, `import-ai` →
  `importai.substack.com/feed`; **removed `anthropic` + `the-batch`** (no public RSS exists — all candidate
  endpoints 404/500; flagged for a future web-reader path). Reddit remains 403 without OAuth creds.
- **The quiet gate (`quiet_day_min_importance=0.40`) is well-placed:** the thin 60h window (max objective
  importance **0.331**) → honestly **quiet**; the busy 96h window (max **0.414**, OpenAI custom chip /
  Anthropic–Alibaba) → **not quiet**. Kept as-is.
- **Breakthrough stays strict (0.72 / importance-override 0.55):** correctly reserved for cross-source
  mega-stories; a single-source post is never auto-"revolutionary" (matches the "truly revolutionary,
  cannot-miss" intent). Kept as-is.
- **Real bug found + fixed — `notable` was unreachable.** `final_rank` is compressed (~0.32–0.49 for
  typical AI stories, because personal-fit ~0.6 dominates and is similar across them), so the old
  `notable_min_score=0.55` could never fire — every day collapsed to all-MINOR, under-calling clearly
  notable stories. Lowered `notable_min_score` to **0.40** (the real scale). The busy window now reads
  **0 breakthrough · 7 notable · 84 minor · not quiet** — a sane, honest shape (daily caps at 15 items).
- **Regression-checked:** full mock suite still 312 pass; the **live golden eval gate still PASSES**
  (busy → breakthrough 4.8, quiet → quiet 4.35), so the flexibility-principle guarantees are intact.

---

## Content quality + live delivery (2026-06-26)

Live delivery verified end-to-end (Resend + Telegram) — and fixing the first real
digests surfaced several quality bugs, now fixed. **317 tests pass**, ruff + mypy clean.

- **AI-relevance gate (`process/relevance.py`).** The HN firehose carried non-AI junk
  (pets, unions, legal disputes, "brain circuits") that embedding-cosine alone let
  through. Added a cheap batched LLM relevance judgment over story titles, applied
  after ranking / before tier classification, so the quiet-day decision is over
  AI-relevant stories only. Live: **86 → 40 AI-relevant** (junk gone). Config
  `AIDIGEST_RELEVANCE_FILTER` (default on; no-op in mock).
- **Dense quiet-day roundup.** Generation now honors the flexibility principle: a
  quiet day renders the honest TL;DR + a **brief roundup of the top ~8 stories as
  one-line takeaways** (not 15 filler "nothing major here" lines, not just 1). A
  busy day keeps full tiers (breakthrough = full depth).
- **Telegram length/format bug.** A full 8-story digest exceeded Telegram's 4096-char
  limit and MarkdownV2 escaping was fragile → HTTP 400. Telegram now gets a
  **condensed plain-text push** (`render_telegram_text`: TL;DR + tier-tagged titles,
  truncated) with the 👍/👎 keyboard; full takeaways stay in email/web.
- **Delivery is fail-soft.** `send_email` + `send_daily` now catch errors → return
  False (one channel can't crash the pipeline).
- **Ingestion quality.** Reddit `.json` is hard-403 from servers → switched the
  no-OAuth path to the free public `/r/<sub>/hot/.rss` (200, rate-limited, no
  metrics; OAuth still preferred). Fixed stale feed URLs (openai/mistral/import-ai);
  dropped Anthropic + The Batch (no public RSS).
- **No-DB preview (`scripts/preview_daily.py`, `make preview`).** Runs the full live
  pipeline in-memory and optionally delivers — used to validate quality before a DB
  exists. Live result: a clean "quiet day + 8 dense AI one-liners" digest delivered
  to email + Telegram (`email=True telegram=True`).
- **Free deploy path documented (`infra/DEPLOY.md`).** No paid Fly Postgres needed:
  GitHub Actions cron + Supabase/Neon free Postgres + Resend free + Telegram = ~$0
  infra (Gemini usage only). Web dashboard optional.

---

## Content overhaul to the smol.ai bar + subagent validation (2026-06-26)

User feedback: digest was "nowhere near smol.ai" — only Community/HN (no academia/industry,
despite being explicitly requested), and low-signal junk ("weird unknown github repos").
Goals written to `CONTENT_GOALS.md`; validated by 3 parallel subagent editors across 2 rounds.

**Diagnosis:** ingestion DOES yield academia (~232/96h) + industry, but (a) the preview capped by
global RECENCY (all HN), and (b) the gate only asked "is it AI?" with no notability bar.

**Fixes:**
- **Editorial curator (`process/curate.py`)** replaces the binary relevance gate: a family- and
  signal-aware LLM pass that keeps significant papers / real lab announcements / substantive
  threads and DROPs self-promo. Plus a hard pre-filter for single-source "Show HN:" / "[P]" posts.
- **Balanced per-family selection (`generate/daily.py`)** — round-robin academia→industry→
  community→meta so academia + industry always get slots (not just whatever ranks highest).
- **Balanced ingest pool (`preview_daily.py`)** — cap each SOURCE (not global recency) so arXiv/HN
  don't crowd out academia/industry.
- **Density**: `daily_map.md` prompt now demands mechanism-level "why it matters" (concrete
  numbers/methods/failure modes; banned generic filler). **Length caps** (`_clip`) fix a real bug
  where a reasoning-model repetition loop produced a 38KB run-on sentence.
- **Format**: 🎓🏭💬🗞️ section emoji in markdown, canonical subfield tag casing, and source-aware
  link anchors (`[arXiv] · [HF] · [Reddit]`, never bare `[link]`).

**Validation outcome (round 2, on the revised digest):** G1 source balance PASS (academia 5 /
industry 2-3 / meta 1; community 0 honestly, that day's community was all self-promo), G2 curation
PASS (all items KEEP-worthy; prior junk gone), G3 AI-relevance PASS (8/8 AI, all map to ≥2
subfields), G5 honest-flexibility PASS, G6 format PASS after the link-anchor fix. G4 density: the
strong items are smol.ai-tier (e.g. "9.64× lossless speedup on MATH-500", "control-token
probability spikes suppress tool calling"); residual thinness on newsletter-sourced items
(GLM-5.2) is source-material-bound and improves in production with the web reader ON (off in the
cost-bounded preview). A real delivered example: 5 relevant arXiv papers (RL skill distillation,
tool-use RL collapse, speculative decoding) + OpenAI/Broadcom chip + Gemini computer-use.
