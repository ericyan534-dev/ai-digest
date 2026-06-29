# `ai-digest` — A Personal AI Intelligence Engine

> *A smol.ai for an audience of one — that also reads arXiv.*
> A self-hosted, LLM-powered pipeline that ingests the AI firehose (community + industry + **academia**),
> compresses it into **daily takeaways**, writes a **weekly NYT-style "Week at a Glance,"** and
> **gets smarter from your feedback**.

Author: drafted with Claude · Target builder: you · Status: **v0 design doc** · Last updated: 2026-06-21

---

## 0. The one-paragraph spec

We are building a personal news engine that watches ~hundreds of sources across four worlds —
**community** (Reddit, HN, Discord), **industry** (lab blogs, X-via-aggregators, top-voice newsletters),
**academia** (arXiv, OpenReview/top conferences, Semantic Scholar), and **meta** (smol.ai itself,
other curators) — then runs an LLM compression pipeline that produces a **short daily digest** and a
**long weekly editorial**, delivered to **email + a web dashboard + a chat bot**, with every item carrying
a 👍/👎 affordance whose signal **re-ranks tomorrow's feed**. It is full-code, self-hosted Python,
Gemini-Flash-first, and built so a single person can operate and continuously improve it.

**North-star metric:** *minutes-to-confidence* — how fast you can read the digest and be sure you missed nothing that matters to **you**.

---

## 1. Design philosophy (the principles that drive every choice)

These are the load-bearing opinions. If a tech choice violates one of these, it's wrong.

1. **The funnel, not the feed.** The job is *compression with recall*, not *collection*. smol.ai ingests
   ~12k messages/day and emits one readable page. Our pipeline is a series of lossy-but-faithful
   reductions: `raw → deduped → clustered into stories → ranked → summarized → written`.

2. **Eval-first, vibes-second.** You cannot improve what you cannot measure. Before the pipeline is
   "smart," it must be *observable and scored*. Every LLM call is traced; the daily output is graded by
   an LLM-judge against a rubric; your 👍/👎 is the ground-truth label set. (This is the single biggest
   difference between a toy and a system that compounds.)

3. **Best-of-N over one-shot.** swyx's "run four pipelines, pick the best" is not a gimmick — it's how you
   get editorial quality out of stochastic models. We bake N-candidate generation + a judge into the
   weekly writer from day one.

4. **Separate *extraction* from *writing*.** Cheap, fast, high-recall models (Flash) do the boring
   high-volume work (summarize-this-thread, classify, embed). Expensive frontier models (Gemini 3.5 Pro /
   Claude Opus) only touch the final ~few thousand tokens where prose quality and judgment matter. This is
   the cost-and-quality unlock.

5. **Structured data end-to-end.** Every stage reads/writes typed objects (Pydantic), never free text
   passed model-to-model. Free text between stages is where pipelines rot.

6. **Idempotent, replayable, cheap to re-run.** Content-hash everything. Re-running yesterday should cost
   ~$0 and produce identical output. This makes debugging and prompt-iteration painless.

7. **Personal ≠ generic-minus-noise.** A generic AI digest already exists (it's smol.ai). Our entire reason
   to exist is the **tailoring**: your subfields, your weighting of academia vs. product news, your taste.
   The personalization layer is the product, not a feature.

---

## 2. System architecture at a glance

```
                          ┌────────────────────────────────────────────────────────────┐
                          │                      ai-digest                             │
                          └────────────────────────────────────────────────────────────┘

  SOURCES                 INGEST            STORE            PROCESS            GENERATE          DELIVER
  ───────                 ──────            ─────            ───────            ────────          ───────
 ┌───────────┐
 │ Community │ Reddit ─┐                 ┌──────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐
 │  HN,Discord│ HN ────┤                 │ Postgres │   │ 1 dedup      │   │ daily      │   │  Email   │
 └───────────┘  Disc ──┤   ┌──────────┐  │  +       │   │   (embed +   │   │  takeaways │   │ (Resend) │
 ┌───────────┐         ├──▶│ fetchers │─▶│ pgvector │──▶│   DBSCAN)    │──▶│  (Flash)   │──▶├──────────┤
 │ Industry  │ blogs ──┤   │ +norma-  │  │          │   │ 2 cluster→   │   ├────────────┤   │  Web app │
 │ labs, X*  │ X*(agg)─┤   │ lizers   │  │  + object│   │   "stories"  │   │ weekly     │   │ (Next.js)│
 │ news'ltrs │ RSS ────┤   └──────────┘  │  storage │   │ 3 enrich     │   │ "Week at a │   ├──────────┤
 └───────────┘         │                 │ (raw     │   │ 4 score +    │   │  Glance"   │   │ Telegram │
 ┌───────────┐         │                 │  text)   │   │   RANK       │   │ (Pro/Opus, │   │   bot    │
 │ Academia  │ arXiv ──┤                 └──────────┘   │   ⇡ feedback │   │  best-of-N)│   ├──────────┤
 │ arXiv,    │ S2 ─────┤                                │   ⇡ profile  │   └────────────┘   │ RSS /    │
 │ OpenReview│ OR ─────┘                                └──────────────┘                    │ wiki     │
 │ conf/jrnl │                                                  ▲                           │ export*  │
 └───────────┘                                                  │  👍/👎, dwell, clicks      └──────────┘
                                                          ┌─────┴───────┐                         │
                                                          │  FEEDBACK   │◀────────────────────────┘
                                                          │  + EVAL     │   (Langfuse traces, LLM-judge,
                                                          └─────────────┘    golden set, prompt iteration)

           Orchestrated by Prefect (self-hosted) · observed by Langfuse · * = v2 / future hook
```

Six stages, one feedback loop. The rest of this doc is each box in detail, with **the pick**, **why**, and **alternatives**.

---

## 3. Ingestion layer — getting the firehose in

The hardest and most valuable layer. Strategy: **one normalized `Item` schema, many small source-adapters.**
Each adapter's only job is `source → list[Item]`. Adding a source = adding a 50-line file. (This is the
"many small files" discipline that keeps ingestion maintainable as you add the 30th source.)

### 3.1 The four source families

| Family | Sources | How to ingest | Tool / API |
|---|---|---|---|
| **Community** | Reddit (r/LocalLLaMA, r/MachineLearning), Hacker News, Discords | Official APIs | **PRAW** (Reddit), **HN Firebase/Algolia API**, **discord.py** (your own bot in chosen servers) |
| **Industry** | Lab blogs (OpenAI/Anthropic/DeepMind/Mistral/Qwen), top-voice newsletters, X | **RSS first**; newsletters via a dedicated inbox + parser; **X via aggregators** (your v1 choice) | `feedparser`, an [n8n/IMAP] email-ingest, smol.ai + HN + Reddit as X-proxies |
| **Academia** | arXiv (cs.CL/cs.LG/cs.AI), OpenReview (ICLR/NeurIPS), Semantic Scholar, HF Daily Papers | Purpose-built APIs (below) | `arxiv` pkg / **OAI-PMH**, **OpenReview API v2**, **S2 Graph API**, HF papers RSS |
| **Meta / curators** | smol.ai AINews, Latent Space, Import AI, Interconnects, The Batch | RSS / scrape | `feedparser` + a reader (Firecrawl/Jina) for paywalled-ish HTML |

### 3.2 Academia — the part smol.ai doesn't do (your edge)

This is your differentiator, so it gets first-class treatment:

- **arXiv** — two ingest modes:
  - *Daily new-papers:* the [arXiv API](https://info.arxiv.org/help/api/index.html) (HTTP GET, Atom XML) filtered by category + date, or the per-category **RSS** feeds. Easiest start: the `arxiv` Python wrapper.
  - *Bulk/robust:* [OAI-PMH](https://info.arxiv.org/help/bulk_data.html) (`ListRecords`, updated daily) to keep a complete, replayable metadata mirror. Use this once you care about not missing anything.
- **Semantic Scholar** — the [Academic Graph API](https://api.semanticscholar.org/api-docs/) (200M+ papers, citation graph). Use it to (a) enrich an arXiv paper with **citation velocity** (a strong "this matters" signal) and (b) pull "what's citing the thing everyone's talking about." Public + rate-limited; grab a free API key for headroom.
- **OpenReview** — the [API v2](https://docs.openreview.net/reference/api-v1) exposes submissions, scores, and reviews for ICLR, NeurIPS, etc. This is how you catch conference papers *during review season*, weeks before camera-ready.
- **Hugging Face Daily Papers** — community-upvoted; a great human-curated relevance prior. (You already have HF MCP tools wired in this environment — `paper_search`, `hf_doc_search` — usable as adapters.)

**Importance signal for papers** = `f(citation velocity, author h-index/affiliation, HF upvotes, social mentions, your subfield match)`. This composite is what lets you surface the 5 papers/day worth knowing out of ~600.

### 3.3 X/Twitter (your v1 decision: aggregators)

You chose **rely-on-aggregators**, which is correct for v1 — most viral X content re-surfaces in smol.ai,
HN, and r/LocalLLaMA within hours. Implementation: treat smol.ai's issue + HN + Reddit as *proxy
ingestion* for X, and extract the URLs/claims they reference. (The community tool
[`ainews-source-extractor`](https://github.com/ThomsenDrake/ainews-source-extractor) already pulls all
URLs out of a smol.ai issue — a handy starting hack.) **v2 upgrade path:** add direct X via a paid
scraper (Apify-style) or the official API, scoped to a curated list of ~50 accounts.

### 3.4 Web reading / extraction fallback

For any source without a clean feed (HTML blogs, JS-heavy pages): a **reader API** that returns clean
markdown. Options: **Firecrawl**, **Exa** (`/contents`), or **Jina Reader** (`r.jina.ai`). You already have
**Exa + Firecrawl-class MCP tools** available — wire one as the universal "give me clean text for this URL"
adapter so the rest of the pipeline never sees raw HTML.

### 3.5 The normalized `Item`

Everything becomes this (Pydantic), content-hashed for idempotency:

```python
class Item(BaseModel):
    id: str                 # = sha256(canonical_url or normalized_text)
    source: str             # "arxiv" | "reddit" | "hn" | "blog:anthropic" | "smol.ai" ...
    family: Literal["community","industry","academia","meta"]
    url: str | None
    title: str
    author: str | None
    published_at: datetime
    fetched_at: datetime
    raw_text: str           # cleaned body (markdown)
    embedding: list[float] | None      # filled in processing
    metrics: dict           # upvotes, comments, citations, hf_upvotes, ...
    raw: dict               # source-specific payload, never lost
```

---

## 4. Storage layer

**The pick: PostgreSQL + [`pgvector`](https://github.com/pgvector/pgvector), plus object storage for raw text/HTML.**

Why one boring database for everything:

- pgvector gives you **embeddings, semantic dedup, clustering, and recsys** in the same place as your
  relational data — no separate vector DB to operate. It comfortably handles the millions-of-vectors scale
  a personal digest will ever reach, and supports the exact ops we need (cosine distance for similarity,
  used for dedup/clustering). Run it as **Supabase** (managed Postgres+pgvector+auth+storage, generous free
  tier) or a single Docker Postgres.
- **Object storage** (Supabase Storage / S3 / local volume) holds full raw bodies and rendered HTML so
  Postgres rows stay lean and re-runs are cheap.

Tables: `items`, `stories` (clusters), `digests` (daily/weekly outputs), `feedback`, `sources`, `eval_runs`.
(Full schema in §10.)

> **Alternatives considered:** dedicated vector DB (Qdrant/Weaviate/LanceDB) — unnecessary operational
> surface at this scale; **SQLite + sqlite-vec** — tempting for true single-file simplicity and fine for v0,
> but you'll want Postgres concurrency once the web app + bot + pipeline all read/write. Start on Postgres.

---

## 5. Processing layer — raw items → ranked stories

This is the "compression with recall" engine. Four steps, mostly cheap models + classic ML.

### 5.1 Embed
Embed every `Item` (title + lead) once. **Pick: Gemini embeddings** (`gemini-embedding-*`) for
stack-consistency with your Flash-first choice; alternatives: OpenAI `text-embedding-3`, or open
**BGE-M3 / Qwen3-Embedding** if you want zero per-call cost on a local GPU. Store in pgvector.

### 5.2 Deduplicate
The same launch shows up as 40 items. Collapse them: cluster by embedding cosine-similarity (**DBSCAN**,
threshold ~0.9 is the well-known sweet spot for semantic dedup), keep the highest-authority representative,
attach the rest as `mentions`. *Mention count across independent sources is itself a top relevance signal.*

### 5.3 Cluster into "stories"
A looser pass (smaller cosine threshold, or HDBSCAN) groups deduped items into **stories** — "the DeepSeek
V4 release," "new long-context attention paper + the discourse around it." A story is the unit the digest
talks about, not the individual item. Optionally label each cluster with a one-line LLM title (cheap Flash call).

### 5.4 Enrich + score + RANK
For each story compute an **importance score** and a **personal score**:

```
importance = w1·cross_source_mentions + w2·source_authority + w3·recency
           + w4·engagement(upvotes/comments) + w5·citation_velocity(papers)
personal   = cos(story_embedding, your_interest_vector)            # see §6
final_rank = α·importance + β·personal + γ·diversity_bonus
```

`diversity_bonus` prevents the digest from being 100% "today's biggest model launch" — it reserves slots
for academia and your niche subfields. The weights `α,β,γ` and `w*` are **tunable knobs your feedback moves**.

---

## 6. Personalization layer — why this is *yours*

The mechanism that turns a generic digest into your digest. Three nested loops, simplest-first:

**Loop 1 — Static profile (day 1).** A YAML you write: subfields you care about (e.g. "mechanistic
interpretability, RL post-training, on-device inference"), voices you trust, things to mute. Embed it →
`interest_vector`. The `personal` score in §5.4 is literally cosine to this. Zero ML, immediate tailoring.

**Loop 2 — Feedback as labels (week 2+).** Every delivered item carries 👍/👎 (email link, web button,
bot reaction). Plus implicit signals: clicks, dwell time, "read more" expansion. Store in `feedback`.
Nightly, recompute `interest_vector` as a **decayed weighted centroid** of embeddings of liked stories
minus disliked ones. This is the FeedRec/GeneRec pattern (LLM-embedding + feedback-embedding) shown to work
for [personalized news](https://arxiv.org/abs/2411.06046) — but you get 80% of it from a centroid, no
training. It's the same dual-encoder idea LinkedIn moved its 2026 feed to, shrunk to one user.

**Loop 3 — Natural-language steering (v2).** A "tune my feed" box: *"less agent-framework drama, more
kernel/systems papers, keep the Karpathy takes."* An LLM converts the instruction into weight deltas + mute
rules (the [CTRL-Rec](https://arxiv.org/pdf/2510.12742) "control recsys with language" pattern). This is the
most *you* it gets — and it doubles as how you correct the system in plain English.

> Cold-start is solved by Loop 1; quality compounds via Loop 2; Loop 3 is the delightful power-user layer.

---

## 7. Generation layer — daily takeaways + weekly editorial

Two distinct products from the same ranked stories. **Map-reduce summarization**, model-routed by cost.

### 7.1 Daily takeaways (cheap, fast, high-recall)
Cadence: every morning. Model: **Gemini 3.5 Flash** ($1.50/$9 per 1M; its 1M-token context means you can
stuff *the whole day's top-N stories with their source text into a single call* — no fragile chunking).

Pipeline (each step a typed call, traced):
1. **Map:** for each top story, Flash writes a 2–4 sentence takeaway + "why it matters to *you*" + links.
   (Personal angle injected from your profile.)
2. **Reduce:** Flash assembles ~10–15 takeaways into the daily, grouped by family (🏭 Industry / 🎓 Academia
   / 🛠️ Community), with a one-line "TL;DR of the day" at top.
3. **Structure:** output is a Pydantic `DailyDigest` (via **`instructor`**), so email/web/bot all render the
   same object. Never parse free text.

### 7.2 Weekly "Week at a Glance" (NYT-style, frontier, best-of-N)
Cadence: Sunday. This is the showpiece, so spend money here. Model: **Gemini 3.5 Pro** (2M context, Deep
Think — lands ~late June 2026) **or Claude Opus** for prose. This is where swyx's *best-of-four* earns its
keep:

1. **Gather:** the week's stories + your engagement (what you 👍'd, read, ignored).
2. **Outline → N drafts:** generate **N=3–4 candidate editorials** with different leads/angles (one
   "biggest-story-first," one "thematic," one "contrarian/what-everyone-missed").
3. **Judge:** an **LLM-as-judge** (different model, rubric: insight, accuracy, narrative, personal-fit)
   scores the candidates; pick the winner, optionally graft the best section from runners-up.
4. **Polish:** one editing pass for voice ("explanatory, lightly opinionated, NYT-meets-Stratechery"),
   plus a "**What I'd actually read this week**" shortlist and a "**On my radar**" academia preview.

The NYT feel comes from: a strong narrative lede, *connecting* stories into themes rather than listing them,
and a consistent editorial voice — encode that voice as a reusable system prompt + 2-3 few-shot exemplars
you refine over time.

### 7.3 Model routing table

| Job | Volume | Model | Why |
|---|---|---|---|
| Embeddings | very high | Gemini embeddings / local BGE | cheap, batched |
| Classify / title clusters | high | Flash (or Flash-Lite) | trivial, fast |
| Per-story daily takeaway | high | **Gemini 3.5 Flash** | 1M ctx, $1.50/$9, 4× faster |
| Weekly editorial (N drafts) | low | **Gemini 3.5 Pro / Claude Opus** | judgment + prose |
| LLM-as-judge (weekly + eval) | low | a *different* frontier model | independence reduces self-bias |

---

## 8. Delivery layer — email + web + bot (+ future wiki)

One `Digest` object, multiple renderers. You selected all three channels.

- **Email** — **[Resend](https://resend.com)** (clean API, React-Email templates, great deliverability) is
  the modern pick for a code-first newsletter; **Buttondown** if you want a subscriber-management product
  with an API (what AINews originally used before moving to its own site `news.smol.ai`). Render `Digest →
  MJML/React-Email → send`.
- **Web app** — **Next.js** (App Router) reading straight from Postgres/Supabase: searchable archive, topic
  filters, and the 👍/👎 + "tune my feed" UI that powers Loop 2/3. This is also where the future
  Karpathy-wiki view lives.
- **Chat bot** — **`python-telegram-bot`** (or a Discord/Slack bot): morning push of the daily, inline 👍/👎
  reactions (cheapest high-quality feedback channel), and "ask a follow-up about item 3" → a small RAG call
  over that story's source text.
- **Future hooks (your "not now" item):** a **Markdown/Obsidian/wiki exporter** — write each weekly issue as
  a linked `.md` note (stories as `[[wikilinks]]`, tags per subfield) so it composes into a Karpathy-style
  personal LLM wiki later. Designing the `Digest` schema with stable IDs + wikilink-friendly slugs *now*
  makes this a renderer you add later, not a migration. Also trivially gives you an **RSS** out.

---

## 9. Orchestration, observability & the improvement loop

### 9.1 Orchestration — **Prefect (self-hosted)**
**The pick: [Prefect](https://www.prefect.io/prefect/open-source).** It's Python-native (decorate your
existing functions; no DSL), gives retries/caching/observability nearly for free, and self-hosts with a
Postgres backend you already run. It's the best fit for "I write Python and want it to become resilient
scheduled flows."

- **v0 shortcut:** **GitHub Actions cron** (`schedule:` triggers) running your script — genuinely enough to
  ship the first daily, zero infra. Graduate to Prefect when you want retries, caching, and a run UI.
- *Alternatives:* **Dagster** if you grow to think of this as an asset graph (papers, stories, digests as
  declarative assets with lineage) — arguably the most "correct" long-term model; **Windmill** if you want
  the fastest self-hostable job UI (3-min Docker). Any of the three is defensible; Prefect is the lowest
  activation energy from plain Python.

Schedules: ingest every 1–3h → process nightly → **daily digest 7:00am** → **weekly digest Sun 8:00am** →
nightly `interest_vector` recompute + eval run.

### 9.2 Observability + eval — **Langfuse**
**The pick: [Langfuse](https://langfuse.com) (open-source, self-hostable).** Trace every LLM call
(tokens, cost, latency, prompt version), manage prompts, run **LLM-as-judge** evaluators on production
digests, and keep **datasets** (your golden set). This is principle #2 made real.

The iteration loop (this is how the thing "constantly improves from feedback"):

```
   feedback (👍/👎, dwell)            Langfuse traces
        │                                  │
        ▼                                  ▼
  ┌───────────────┐   weekly review  ┌──────────────────┐
  │ golden dataset│◀─────────────────│ eval scores      │
  │ (good/bad     │                  │ (LLM-judge:       │
  │  digests,     │   regression     │  recall, insight, │
  │  must-include │   test on each   │  personal-fit)    │
  │  items)       │   prompt change  └──────────────────┘
  └──────┬────────┘                          ▲
         │                                   │
         ▼                                   │
   prompt / weight change ── A/B via promptfoo ┘
```

- **[promptfoo](https://promptfoo.dev)** for offline A/B of prompt/weight changes against the golden set
  before they ship — so you never regress the digest while "improving" it.
- **Did-I-miss-anything eval:** the highest-stakes failure is *omission*. Add a nightly check where a
  frontier model reads the raw top-50 and asks "what important story did the digest drop?" → that becomes a
  recall metric and a source of new golden labels.

---

## 10. Data model (the typed spine)

```python
# the four core tables, simplified
Item     (id, source, family, url, title, author, published_at,
          fetched_at, raw_text, embedding vector(N), metrics jsonb, raw jsonb)
Story    (id, title, family, item_ids[], representative_item_id,
          embedding vector(N), importance float, created_at)
Digest   (id, kind["daily"|"weekly"], date, story_ids[],
          content jsonb,        # the rendered DailyDigest/WeeklyDigest object
          model, cost_usd, eval_scores jsonb)
Feedback (id, user="me", target_id, target_kind["item"|"story"|"digest_section"],
          signal["up"|"down"|"click"|"dwell"|"nl_instruction"], value, created_at)
```

Everything downstream (renderers, personalization, eval) reads these. Add `embedding` as a `pgvector`
column with an HNSW index for fast similarity.

---

## 11. Suggested repo layout

```
ai-digest/
├── adapters/            # one tiny file per source  (reddit.py, arxiv.py, smolai.py, ...)
│   └── base.py          #   Adapter protocol: fetch() -> list[Item]
├── core/
│   ├── models.py        # Pydantic: Item, Story, Digest, Feedback
│   ├── db.py            # Postgres/pgvector access (repository pattern)
│   ├── embed.py
│   ├── dedup.py         # DBSCAN dedup + clustering
│   └── rank.py          # importance + personal + diversity scoring
├── personalize/
│   ├── profile.yaml     # your static interests (Loop 1)
│   └── interest.py      # centroid recompute (Loop 2), NL steering (Loop 3)
├── generate/
│   ├── daily.py         # Flash map-reduce -> DailyDigest
│   ├── weekly.py        # best-of-N + judge -> WeeklyDigest
│   └── prompts/         # versioned prompt templates (+ voice exemplars)
├── deliver/
│   ├── email.py  web/ (next.js)  bot.py  wiki_export.py  rss.py
├── eval/
│   ├── golden/          # golden dataset
│   ├── judge.py         # LLM-as-judge rubrics
│   └── promptfoo.yaml
├── flows/               # Prefect flows: ingest / process / daily / weekly / nightly
└── infra/               # docker-compose (postgres+pgvector, langfuse), gh-actions
```

`adapters/` and `prompts/` are designed to grow; everything else stays small (principle: many small files).

---

## 12. Build roadmap (ship something every week)

**v0 — "It emails me something" (week 1).** GitHub Actions cron → 3 adapters (HN, r/LocalLLaMA, arXiv
cs.CL) → SQLite/Postgres → Flash one-shot summary → Resend email. No dedup, no ranking. *Goal: feel the
loop.*

**v1 — "It's actually good" (weeks 2–4).** Add pgvector + embed/dedup/cluster; static-profile ranking
(Loop 1); proper daily map-reduce; add ~15 sources incl. lab blogs, smol.ai, OpenReview, Semantic Scholar;
move to Prefect; add Langfuse tracing. Ship the **web archive**.

**v2 — "It's mine and it learns" (weeks 5–8).** 👍/👎 across all channels → feedback centroid (Loop 2);
the **weekly best-of-N NYT editorial**; Telegram bot; LLM-judge eval + golden set + promptfoo regression
gate; NL steering (Loop 3).

**v3 — "Power features" (later).** Direct X ingestion; podcast/YouTube transcript adapters; the
Karpathy-wiki Markdown export + `[[wikilinks]]`; multi-issue "trend tracking" (how a topic's volume moves
over weeks); "did-I-miss-anything" recall eval.

---

## 13. Cost sketch (your budget is flexible, but for sanity)

Dominated by Flash, which is cheap. Rough daily: embeddings + classify ≈ pennies; daily map-reduce over
~200k tokens in / ~5k out on Flash ≈ **~$0.30–0.50/day**. Weekly frontier best-of-N editorial ≈
**~$1–3/issue**. Infra: Supabase + GitHub Actions + self-hosted Langfuse/Prefect ≈ **$0–25/mo**. Total
**well under ~$40/mo** at personal scale — and prompt-caching ($0.15 cached input on Flash) plus idempotent
re-runs keep it there.

---

## 14. Open questions for you (these change the build)

1. **Voice sample.** The NYT-style weekly lives or dies on its voice. Can you point me at 2–3 writers/issues
   whose tone you want (e.g. Stratechery, Import AI, Karpathy threads, actual NYT)? I'll turn them into the
   editorial system prompt + few-shot exemplars.
2. **Your subfields, concretely.** Give me your top ~5 research/industry areas and ~10 must-follow voices so
   I can seed `profile.yaml` and the source list precisely (this is the personalization that smol.ai can't do).
3. **Academia depth.** Do you want *paper-level* coverage (individual arXiv papers summarized) or
   *theme-level* (clusters/trends only)? Changes how aggressive dedup/clustering is for the academia family.
4. **Web app build vs. buy.** Full custom Next.js dashboard, or start with a near-zero-effort surface
   (a static archive site generated from Markdown, à la `news.smol.ai`) and add interactivity later?
5. **Should I scaffold v0 now?** I can generate the repo skeleton (adapters protocol, models, a working
   HN+arXiv→Flash→email path) in this `ai_digest/` folder so you have something running this week.

---

## 15. References

**smol.ai / the pattern we're cloning**
- AI News (now self-hosted): https://news.smol.ai/ · company: https://smol.ai/ · original Buttondown archive: https://buttondown.com/ainews
- Community URL-extractor for AI News issues: https://github.com/ThomsenDrake/ainews-source-extractor

**Models**
- Gemini API pricing (3.5 Flash, 3.1 Pro, etc.): https://ai.google.dev/gemini-api/docs/pricing · analysis: https://www.metacto.com/blogs/the-true-cost-of-google-gemini-a-guide-to-api-pricing-and-integration

**Academia ingestion**
- arXiv API: https://info.arxiv.org/help/api/index.html · bulk/OAI-PMH: https://info.arxiv.org/help/bulk_data.html
- Semantic Scholar Graph API: https://api.semanticscholar.org/api-docs/
- OpenReview API: https://docs.openreview.net/reference/api-v1

**Storage / processing**
- pgvector: https://github.com/pgvector/pgvector · guide: https://northflank.com/blog/postgresql-vector-search-guide-with-pgvector

**Personalization research**
- LLM-embedding news rec: https://arxiv.org/abs/2411.06046 · FeedRec (varied feedback): https://arxiv.org/pdf/2102.04903 · CTRL-Rec (NL control): https://arxiv.org/pdf/2510.12742

**Orchestration / observability**
- Prefect OSS: https://www.prefect.io/prefect/open-source · Dagster: https://dagster.io/ · Windmill: https://www.windmill.dev/
- Langfuse: https://langfuse.com/ (repo: https://github.com/langfuse/langfuse) · promptfoo: https://promptfoo.dev/

**Delivery**
- Resend: https://resend.com · Buttondown: https://buttondown.com · python-telegram-bot: https://python-telegram-bot.org
