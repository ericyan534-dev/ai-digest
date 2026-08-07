# Deployment Runbook

## Prerequisites

This repo is not yet a git repository. Before any of the steps below work:

```bash
git init
git add .
git commit -m "chore: initial commit"
# Create a repo on GitHub, then:
git remote add origin https://github.com/YOUR_ORG/ai-digest.git
git push -u origin main
```

---

## 0. Cost & architecture — the FREE path

You do **not** need paid Fly Postgres ($25–38/mo). The core product (daily +
weekly digest to email + Telegram) runs at **$0 infra**:

| Piece | Free option | Notes |
|---|---|---|
| Scheduled pipeline | **GitHub Actions cron** | free for public + generous for private repos |
| Database (Postgres + pgvector) | **Supabase** or **Neon** free tier | 0.5 GB — ample for a personal digest |
| Email | **Resend** free | 3,000 emails/mo, 100/day |
| Telegram | Bot API | free |
| LLM | Gemini API | ~$0.30–0.50/day usage (the only real cost) |

The always-on **web dashboard is optional** — skip it and read the digest in
email/Telegram, or deploy it later (frontend on Vercel free; API on Fly with
`auto_stop_machines` so it scales to zero, or Render's free web service).

So the minimal free deploy is just: **GitHub repo + Actions secrets + a free
Supabase/Neon DB.** No Fly account required for the digest itself.

---

## 1. Provision a FREE Postgres with pgvector

**Option A — Supabase (recommended; pgvector built in):**

1. Create a project at https://supabase.com (free tier).
2. pgvector is enabled by default — nothing to install.
3. Settings → Database → Connection string (**URI**, "Session pooler" is fine) →
   that's your `DATABASE_URL`.

**Option B — Neon (serverless, free tier):**

1. Create a project at https://neon.tech (free tier).
2. Enable pgvector once: in the Neon SQL editor run `CREATE EXTENSION IF NOT EXISTS vector;`
3. Copy the connection string (with `?sslmode=require`) → `DATABASE_URL`.

**Option C — Fly Postgres (paid; only if you're already all-in on Fly):**

```bash
fly postgres create --name ai-digest-db --region sjc
fly postgres attach ai-digest-db --app ai-digest-api   # sets DATABASE_URL
fly ssh console --app ai-digest-db -C "psql -U postgres -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
```

Then apply the schema once (idempotent): `DATABASE_URL=... (cd backend && python -m scripts.migrate)`.

---

## 2. Run Migrations

Run the schema migration once against the managed DB before first deploy:

```bash
DATABASE_URL="<your-connection-string>" \
  cd backend && python -m scripts.migrate
```

Fly will also run `python -m scripts.migrate` automatically on every deploy
via the `release_command` in `backend/fly.toml`.

---

## 3. Deploy Backend to Fly.io

The Fly config lives at `backend/fly.toml` so the build context is `backend/`
(the Dockerfile's COPY paths resolve there).

```bash
# First-time setup (run from repo root):
fly launch --config backend/fly.toml --no-deploy

# Set required secrets:
fly secrets set \
  DATABASE_URL="<your-postgres-connection-string>" \
  GEMINI_API_KEY="<your-gemini-key>" \
  --app ai-digest-api

# Optional delivery secrets:
fly secrets set \
  RESEND_API_KEY="<key>" \
  DIGEST_FROM_EMAIL="digest@yourdomain.com" \
  DIGEST_TO_EMAIL="you@yourdomain.com" \
  TELEGRAM_BOT_TOKEN="<token>" \
  TELEGRAM_CHAT_ID="<chat-id>" \
  TELEGRAM_WEBHOOK_SECRET="<random-string>" \
  --app ai-digest-api

# Optional security + richer ingestion secrets:
fly secrets set \
  AIDIGEST_API_KEY="<random-string>" \
  AIDIGEST_LINK_SECRET="<random-string>" \
  S2_API_KEY="<semantic-scholar-key>" \
  REDDIT_CLIENT_ID="<id>" \
  REDDIT_CLIENT_SECRET="<secret>" \
  --app ai-digest-api

# Deploy:
fly deploy --config backend/fly.toml
```

If you set `AIDIGEST_API_KEY`, register the Telegram webhook so button feedback
is recorded (run once):

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://ai-digest-api.fly.dev/api/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

The health check is `/api/health`. Verify with:

```bash
curl https://ai-digest-api.fly.dev/api/health
```

---

## 4. Deploy Frontend to Fly.io

The frontend build bakes `NEXT_PUBLIC_API_BASE` into the JS bundle at build time.

```bash
# First-time setup:
fly launch --config frontend/fly.toml --no-deploy

# Deploy with the backend URL as a build arg:
fly deploy --config frontend/fly.toml \
  --build-arg NEXT_PUBLIC_API_BASE=https://ai-digest-api.fly.dev

# If the backend has AIDIGEST_API_KEY set, give the web app the SAME key as a
# server-only secret so its proxy routes (/app/api/*) can forward it:
fly secrets set AIDIGEST_API_KEY="<same-key>" --app ai-digest-web
```

To change the API URL later, update `frontend/fly.toml` `[build.args]` and redeploy.

---

## 5. GitHub Repository Secrets for Cron (digest.yml)

Add these in: GitHub repo > Settings > Secrets and variables > Actions > New repository secret

| Secret | Required | Description |
|--------|----------|-------------|
| `DATABASE_URL` | Yes | Persistent Postgres connection string |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `AIDIGEST_TIMEZONE` | No | IANA timezone, e.g. `America/Los_Angeles` |
| `AIDIGEST_PUBLIC_BASE_URL` | No | API public URL for email feedback links |
| `AIDIGEST_LINK_SECRET` | No | HMAC secret signing email feedback links |
| `RESEND_API_KEY` | No | Resend email delivery key |
| `DIGEST_FROM_EMAIL` | No | Sender email address |
| `DIGEST_TO_EMAIL` | No | Recipient email address |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token |
| `TELEGRAM_CHAT_ID` | No | Telegram chat/channel ID |
| `S2_API_KEY` | No | Semantic Scholar key (higher rate limit) |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | No | Reddit app-only OAuth |

**Important:** `DATABASE_URL` must point to the same managed Postgres used by the
API — not an ephemeral CI database. The pipeline accumulates data across runs;
an empty per-run DB would break the product. The `daily` and `weekly` cron jobs
run with `--deliver`, so email/Telegram are sent when those secrets are present.

---

## 6. Cron Schedule Reference

| Job | Cron (UTC) | Nominal Local (PDT) | Notes |
|-----|-----------|---------------------|-------|
| Ingest | `0 */3 * * *` | Every 3 hours | |
| Daily digest | `0 14 * * *` | 07:00 | idempotent (`--if-missing`) |
| Daily catch-up | `0 17 * * *`, `0 20 * * *` | 10:00, 13:00 | no-op unless the day never shipped |
| Weekly digest | `0 15 * * 0` | Sunday 08:00 | idempotent (`--if-missing`) |
| Weekly catch-up | `0 18 * * 0` | Sunday 11:00 | no-op unless the week never shipped |
| Nightly recompute | `0 11 * * *` | 04:00 | |

To trigger any job manually: GitHub Actions > Digest Pipeline > Run workflow > select job.
`daily` / `weekly` there are **force** runs — they regenerate and re-send even if the digest
already shipped. `daily-catchup` / `weekly-catchup` exercise the same gated path cron uses.

### Scheduled runs are late, and sometimes never happen

Two operational facts about GitHub-hosted cron, both measured on this repo — plan around them
rather than assuming a cron fires on time:

1. **Delay of 1–2.5 h is normal.** Scheduled workflows sit in a shared queue. Ten consecutive
   daily slots (`0 14 * * *`, 2026-07-27 → 08-05) actually started at 14:59–16:24 UTC: median
   ≈ 2 h late, max 2 h 25 m. The digest therefore lands nearer 09:00 PDT than the nominal 07:00.
   Moving the cron earlier trades a late digest for an erratic one — the delay varies by an hour
   or more day to day — so the schedule is left honest and this table states the nominal time.
2. **A slot can be dropped entirely.** On 2026-08-06 the daily run (`31118610124`) and the
   following ingest (`31120872463`) both failed with *"The job was not acquired by Runner of type
   hosted even after multiple attempts"*: GitHub could not allocate a runner, so the job never
   started, no application code ran, and no logs exist. Nothing in the pipeline noticed and
   **no digest was produced or delivered for that day.**

The catch-up schedules exist for case 2. Every scheduled daily/weekly run passes `--if-missing`,
which makes it a strict no-op when that date's digest has already been generated *and* delivered,
so a later slot safely re-attempts a dropped one. Delivery is recorded in the `app_state` table
(`delivery_daily` / `delivery_weekly`); a run in progress writes `claim_daily` / `claim_weekly`
so two overlapping runs cannot both send. A claim older than 30 minutes is treated as stale, so a
crashed run cannot wedge the catch-up.

### When something fails

The `alert` job messages Telegram (when `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are set) with a
link to the run whenever the pipeline job ends in any state other than success. It deliberately
fires on `cancelled` as well as `failure`: a runner that is never acquired reports the job as
**cancelled**, so a bare `if: failure()` would have missed the 2026-08-06 incident entirely.

Recovery, in order of preference:

1. **Do nothing** — the next catch-up slot re-attempts it automatically.
2. Run it now: Actions > Digest Pipeline > Run workflow > `daily-catchup` (safe; skips if the
   digest already shipped).
3. Force a regeneration and re-send: same menu, `daily`.

A digest missed for a whole past day is not back-filled: stories are bucketed by the day they were
first processed, so re-running for an old date cannot reconstruct that day's story set. Yesterday's
still-recent items simply surface in today's digest instead.
