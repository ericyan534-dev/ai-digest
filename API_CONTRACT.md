# API_CONTRACT.md — REST contract (frontend ↔ backend)

Base URL: `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`). All paths
prefixed `/api`. All responses JSON, UTF-8. Datetimes are ISO-8601 strings.
Enums are lowercase strings matching `aidigest.models`.

Backend serializes domain models with `model_dump(mode="json")`, so JSON field
names match the Pydantic field names exactly (see shapes below).

---

## GET /api/health

Liveness + dependency check.

```json
{ "status": "ok", "db": "ok", "llm_mock": true, "version": "0.1.0" }
```
`db` is `"ok"` or `"down"`. `200` always when the process is up.

---

## GET /api/digests?kind=&limit=

List digest summaries for the archive (newest first).

Query params:
- `kind` (optional): `"daily"` | `"weekly"`. Omit for both.
- `limit` (optional, default `30`, max `100`).

Response: `200` — array of summary rows:
```json
[
  {
    "id": "daily-2026-06-21",
    "kind": "daily",
    "date": "2026-06-21",
    "tier": "notable",
    "quiet": false,
    "title": "TL;DR or weekly headline",
    "created_at": "2026-06-21T14:03:00Z"
  }
]
```
`title` = the daily `tldr` for dailies, the weekly `title` for weeklies.

---

## GET /api/digest/{id}

Full digest by id. Shape depends on `kind`.

**Daily** (`DailyDigest`):
```json
{
  "id": "daily-2026-06-21",
  "kind": "daily",
  "date": "2026-06-21",
  "tldr": "Quiet day — nothing major shipped.",
  "overall_tier": "quiet_day",
  "quiet_day": true,
  "sections": [
    {
      "family": "academia",
      "heading": "🎓 Academia",
      "summaries": [
        {
          "story_id": "deepseek-v4-ab12cd",
          "title": "DeepSeek V4 released",
          "family": "academia",
          "tier": "breakthrough",
          "takeaway": "2-4 sentences (longer when breakthrough).",
          "why_it_matters": "Tied to your subfields…",
          "links": ["https://…"],
          "tags": ["LLMs", "Optimization"],
          "score": 0.91
        }
      ]
    }
  ],
  "story_ids": ["deepseek-v4-ab12cd"],
  "model": "gemini-3.5-flash",
  "cost_usd": 0.0,
  "eval_scores": {},
  "created_at": "2026-06-21T14:03:00Z"
}
```

**Weekly** (`WeeklyDigest`):
```json
{
  "id": "weekly-2026-W25",
  "kind": "weekly",
  "week_of": "2026-06-15",
  "title": "The week reasoning got cheap",
  "lede": "A strong narrative opening…",
  "body_markdown": "# … full NYT-style editorial in markdown …",
  "overall_tier": "notable",
  "quiet_week": false,
  "shortlist": [
    { "title": "…", "url": "https://…", "one_liner": "…", "family": "academia" }
  ],
  "on_my_radar": [
    { "title": "…", "url": "https://…", "one_liner": "…", "family": "academia" }
  ],
  "story_ids": ["…"],
  "candidate_count": 3,
  "winning_candidate": 1,
  "model": "gemini-3.5-flash",
  "judge_model": "gemini-3.5-flash",
  "cost_usd": 0.0,
  "eval_scores": { "insight": 4.2, "accuracy": 4.6, "narrative": 4.0, "personal_fit": 4.3, "honesty": 5.0 },
  "created_at": "2026-06-22T15:00:00Z"
}
```

`404` if not found: `{ "detail": "digest not found" }`.

---

## GET /api/stories?date=YYYY-MM-DD

Ranked stories for a date (newest digest's universe). `date` optional (defaults
to today, server timezone).

Response: `200` — array of `Story`:
```json
[
  {
    "id": "deepseek-v4-ab12cd",
    "title": "DeepSeek V4 released",
    "family": "industry",
    "item_ids": ["<hash>", "<hash>"],
    "representative_item_id": "<hash>",
    "embedding": null,
    "importance": 0.82,
    "personal": 0.74,
    "final_rank": 0.79,
    "tier": "breakthrough",
    "mention_count": 7,
    "created_at": "2026-06-21T13:00:00Z"
  }
]
```
Note: `embedding` is returned as `null` over the wire (never ship 1536 floats to the UI).

---

## POST /api/feedback

Record a 👍/👎, click, dwell, or NL instruction. Powers ranking (Loop 2).

Request body:
```json
{
  "target_id": "deepseek-v4-ab12cd",
  "target_kind": "story",
  "signal": "up",
  "value": 1.0,
  "text": null
}
```
- `target_kind`: `"item"|"story"|"digest_section"|"digest"`.
- `signal`: `"up"|"down"|"click"|"dwell"|"nl_instruction"`.
- `value`: number. `+1/-1` for up/down, seconds for dwell, `1.0` for click.
- `text`: required only when `signal == "nl_instruction"`.

Response `200`:
```json
{ "ok": true, "id": 123 }
```
Validation error `422` (FastAPI default shape).

---

## POST /api/tune

Natural-language feed steering (Loop 3). Converts an instruction into an updated
profile (server applies + persists session profile).

Request body:
```json
{ "instruction": "less agent-framework drama, more kernel/systems papers, keep the Karpathy takes" }
```

Response `200`:
```json
{
  "ok": true,
  "profile": { "subfields": ["…"], "mutes": ["…"], "ranking": { "alpha": 0.5, "beta": 0.45, "gamma": 0.05 } }
}
```
The returned `profile` is the adjusted profile dict (frontend may display a
confirmation of what changed).

---

## Error envelope

Errors use FastAPI's default: `{ "detail": "<message>" }` with the appropriate
status code (`404`, `422`, `500`). `500` bodies must NOT leak secrets or stack
traces in production.

---

## CORS

The API enables CORS for the Next.js dev origin (`http://localhost:3000`) and
`NEXT_PUBLIC_API_BASE`'s origin. Methods: `GET, POST, OPTIONS`.
