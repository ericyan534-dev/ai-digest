# ai-digest frontend — "Hybrid Editorial"

The web app for **ai-digest**, a self-hosted, single-user personal AI-news
engine. Next.js (App Router) + TypeScript + Tailwind. It consumes the FastAPI
backend described in [`../API_CONTRACT.md`](../API_CONTRACT.md) and renders the
"Hybrid Editorial" design from [`../ACCEPTANCE.md`](../ACCEPTANCE.md).

## Run

```bash
cd frontend
npm install
npm run dev      # dev server on http://localhost:3000
```

Production:

```bash
npm run build    # gate item (f) — must succeed
npm run start    # serve the production build on :3000
```

The backend must be reachable for live data; without it the app renders graceful
empty / quiet-day states.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | FastAPI base URL (preferred) |
| `NEXT_PUBLIC_API_URL` | — | Fallback if `NEXT_PUBLIC_API_BASE` is unset |

Copy `.env.example` to `.env.local` to override:

```bash
cp .env.example .env.local
```

## Views

| Route | View | Source |
|---|---|---|
| `/` | **Today** — daily digest, family sections, 👍/👎, filters, Tune-my-feed | `GET /api/digests?kind=daily`, `GET /api/digest/{id}` |
| `/week` | **Week at a Glance** — weekly editorial, lede, shortlist, on-my-radar | `GET /api/digests?kind=weekly`, `GET /api/digest/{id}` |
| `/archive` | **Archive** — searchable/filterable list of past digests | `GET /api/digests` |
| `/digest/[id]` | Any digest by id (daily or weekly) | `GET /api/digest/{id}` |
| `/story/[id]` | **Story detail** — takeaway, why-it-matters, sources, 👍/👎 | `GET /api/stories`, latest daily |

## Interactions (all wired to the backend)

- **👍 / 👎** — `FeedbackButtons` POST `/api/feedback` (optimistic; reverts on error).
- **Tune my feed** — `TuneFeed` POSTs `/api/tune` and shows what changed.
- **Expand / collapse** — `ExpandToggle` reveals full-depth context for
  BREAKTHROUGH-tier stories (accessible `aria-expanded`/`aria-controls`).
- **Filter chips** — `FamilyFilter` filters by family + subfield (client-side).
- **Quiet-day honesty** — `QuietDayNotice` renders the honest state when
  `quiet_day` / `quiet_week` is true. No manufactured importance.

## Design tokens (`tailwind.config.ts` + `app/globals.css`)

- **Type**: Source Serif (`--font-serif`, Georgia fallback) for headlines/body;
  IBM Plex Mono (`--font-mono`, `ui-monospace` fallback) for datelines, labels,
  tags, metadata. Loaded via `next/font/google`.
- **Color**: paper `#FAF8F3`, ink `#1A1A1A`, accent (oxblood) `#8B2E2E`, muted
  `#6B6660`, hairline `#E3DED4`. One accent only, used sparingly.
- **Feel**: minimal, retro, dense-but-airy (line-height ~1.6).

## Accessibility

- Semantic landmarks (`header`/`nav`/`main`/`footer`/`article`/`section`), a
  skip-to-content link, labelled buttons (`aria-label`/`aria-pressed`), visible
  `:focus-visible` outlines, `aria-live` regions for feedback/tune results, and
  `prefers-reduced-motion` support.
- Responsive from mobile to desktop (single column → measured editorial column).

## Structure

```
app/
  layout.tsx            # fonts + theme + chrome
  page.tsx              # Today
  week/page.tsx         # Week at a Glance
  archive/page.tsx      # Archive
  digest/[id]/page.tsx  # any digest by id
  story/[id]/page.tsx   # Story detail
  globals.css           # theme tokens
  loading.tsx error.tsx not-found.tsx
components/             # DigestHeader, StoryCard, FeedbackButtons, FamilyFilter,
                       # TuneFeed, ExpandToggle, QuietDayNotice, Shortlist, ...
lib/
  api.ts               # typed fetch client (base URL from env)
  types.ts             # wire types mirroring the API contract
  families.ts format.ts markdown.tsx
```

> Note: the API never ships embeddings (`embedding` is `null` over the wire);
> the frontend never requests or renders them.
