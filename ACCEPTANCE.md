# ACCEPTANCE.md — the quality gate

The whole build must pass this checklist before it is "done". Each item is
concrete and testable. CI runs (a)–(e) and (h)–(i) with no network (MOCK mode);
(f) builds the frontend; (g) is a manual/opt-in LIVE smoke test.

## Gate checklist

- [ ] **(a) Infra up.** `make up` brings up Postgres 16 + pgvector
      (`pgvector/pgvector:pg16`) via `infra/docker-compose.yml`; healthcheck
      passes (`pg_isready`).
- [ ] **(b) Migrations apply.** `make migrate` runs `scripts/migrate.py` ->
      `Repo.init_schema()` applying `db/schema.sql` idempotently (re-runnable, no
      errors). `vector` extension enabled; HNSW indexes created.
- [ ] **(c) Pipeline E2E in MOCK mode, zero network.** With `AIDIGEST_LLM_MOCK=1`
      the full pipeline (`run_ingest`→`run_process`→`run_daily`/`run_weekly`)
      produces a VALID `DailyDigest` AND `WeeklyDigest` (Pydantic-valid), with no
      outbound HTTP. Re-running is idempotent (same ids).
- [ ] **(d) Tests pass with flexibility-tier coverage.** `make test` (pytest,
      unit + integration) is green; coverage ≥ 80% overall AND explicitly covers
      tier logic: a QUIET_DAY input yields `quiet_day=True` + an honest TL;DR; a
      BREAKTHROUGH input yields a longer takeaway than a MINOR one; `classify_day`
      and `classify_tier` are unit-tested at every boundary.
- [ ] **(e) API serves every endpoint.** All `API_CONTRACT.md` routes respond with
      the documented shapes (`/api/health`, `/api/digests`, `/api/digest/{id}`,
      `/api/stories`, `POST /api/feedback`, `POST /api/tune`). Tested with
      FastAPI TestClient against the mock LLM + a test DB (or repo fake).
- [ ] **(f) Frontend builds + renders.** `cd frontend && npm install && npm run build`
      succeeds. Today / Week at a Glance / Archive / Story-detail render from the
      API; 👍/👎 buttons POST `/api/feedback`; "Tune my feed" POSTs `/api/tune`;
      expand/collapse + filter chips work; responsive + accessible (labels, focus).
- [ ] **(g) LIVE smoke (real gemini-3.5-flash).** With a real `GEMINI_API_KEY` and
      `AIDIGEST_LLM_MOCK=0`, `make smoke` generates a coherent daily digest over a
      small fixed item set (handles thought tokens + MAX_TOKENS gracefully; honors
      tier; retries on TLS resets).
- [ ] **(h) No hardcoded secrets.** `GEMINI_API_KEY` (and all keys) read only from
      env/settings. `grep -r "AIza" backend/` finds nothing. `.env` untouched.
- [ ] **(i) Lint + types clean.** `make lint` = `ruff check backend` AND
      `mypy backend/aidigest` both clean.

## How to run the gate

```bash
make up          # (a)
make migrate     # (b)
AIDIGEST_LLM_MOCK=1 make daily weekly   # (c)
make test        # (d) + (e)
make web         # (f)  -> then `npm run build` in frontend/
make smoke       # (g)  needs real key
make lint        # (i)
```

---

## EDITORIAL RUBRIC (used by `eval/judge.py`; source of truth is `eval/rubric.py`)

Each criterion scored **1–5**. Weighted aggregate decides the weekly best-of-N
winner and grades nightly dailies.

| Criterion | Weight | What it measures |
|---|---|---|
| **insight** | 0.25 | Non-obvious connections, "what everyone missed", synthesis over listing. |
| **accuracy** | 0.25 | Claims faithful to sources; no hallucinated numbers/results/attributions. |
| **narrative** | 0.20 | Strong lede; themes not lists; consistent voice (fast, dense, plain, lightly opinionated; no marketing adjectives). |
| **personal_fit** | 0.20 | Tailored to the user's subfields (Multi-Agent Systems; Efficient & Scalable NLP; RL for NLP; LLMs & Foundation Models; Optimization) and venues (NeurIPS, ACL). "Why it matters to you" is concrete. |
| **honesty / quiet-day** | 0.10 | Honors the flexibility principle (below). |

### Quiet-day honesty check (HARD GATE)

A digest that violates this is capped at score **2.0** regardless of other
criteria (`QUIET_DAY_CHECK.violation_score_cap` in `eval/rubric.py`):

- If the input had **no** BREAKTHROUGH/NOTABLE stories, the digest MUST say so
  plainly (e.g. *"Quiet day — nothing major shipped"*) and MUST NOT inflate minor
  items into headlines.
- If the input had **no important paper**, say that too.
- A genuine **BREAKTHROUGH** MUST be covered at full depth and not buried.
- Otherwise: briefly summarize the trend (one line). Same policy applies to
  applied/industry AI.

This flexibility logic is real code (`generate/importance.py`:
`classify_tier` / `classify_day`), reflected in the daily/weekly prompts
(which instruct the model to honor the active `ImportanceTier`), and covered by
gate item (d).

---

## UI DESIGN TOKENS — "Hybrid Editorial" (frontend implementer)

Encode these as Tailwind theme tokens / CSS variables.

**Typography**
- Headlines/body: **Source Serif** (fallback Georgia, serif).
- Datelines, labels, tags, metadata: **IBM Plex Mono** (fallback `ui-monospace`, monospace).

**Color**
- Background (paper white): `#FAF8F3`
- Ink (near-black text): `#1A1A1A`
- Accent (deep oxblood / ink-red), ONE accent only: `#8B2E2E`
- Muted/secondary text: `#6B6660`
- Hairline rules / borders: `#E3DED4`

**Feel**: minimal, retro, dense but airy leading (line-height ~1.6 body). Generous
vertical rhythm. Accent used sparingly (links, active filter chips, 👍 active state).

**Views (required)**
- **Today** — the daily digest; family-grouped sections; per-story 👍/👎; expand/
  collapse for full-depth (BREAKTHROUGH) items; filter chips by family + subfield;
  "Tune my feed" text box (POST `/api/tune`). Honest quiet-day rendering when
  `quiet_day` is true.
- **Week at a Glance** — the weekly editorial (rendered `body_markdown`), lede,
  "What I'd actually read this week" shortlist, "On my radar" academia preview.
- **Archive** — searchable/filterable list of past digests (`GET /api/digests`).
- **Story detail** — one story, its items/links, 👍/👎.

**Interaction (required)**: 👍/👎 POST feedback (optimistic UI ok); expand/collapse;
family/subfield filter chips; "Tune my feed". Responsive (mobile→desktop) and
accessible (semantic landmarks, button labels, visible focus, sufficient contrast).
