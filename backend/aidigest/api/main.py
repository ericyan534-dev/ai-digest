"""FastAPI serving layer for ai-digest.

Implements exactly the routes in API_CONTRACT.md, reading through the
repository (`aidigest.db.repo`). Domain models are serialized with
`model_dump(mode="json")` so JSON field names match the Pydantic fields.

Runs end-to-end in MOCK mode (AIDIGEST_LLM_MOCK=1) with no network. The DB is
optional for liveness: `/api/health` reports `db: "down"` instead of crashing
when Postgres is unreachable.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from aidigest.api.schemas import FeedbackRequest, TuneRequest
from aidigest.api.security import rate_limit, require_api_key, verify_click_signature
from aidigest.config import get_settings
from aidigest.db.repo import Repo, get_repo
from aidigest.deliver.telegram_bot import answer_callback_query, extract_feedback_from_update
from aidigest.models import DigestKind, Feedback, FeedbackSignal, FeedbackTargetKind
from aidigest.personalize.feedback import apply_nl_instruction
from aidigest.personalize.profile import load_profile

logger = logging.getLogger("aidigest.api")

VERSION = "0.1.0"
DEFAULT_DIGEST_LIMIT = 30
MAX_DIGEST_LIMIT = 100


def _cors_origins() -> list[str]:
    """Allowed browser origins: the Next.js dev server + the public API origin."""
    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # NEXT_PUBLIC_API_BASE may be served from a non-default origin; keep localhost too.
    base = "http://localhost:8000"
    if base not in origins:
        origins.append(base)
    return origins


# In-process session profile (Loop 3). Seeded from profile.yaml; the persisted
# NL-steered override (when present) is layered on at startup; `POST /api/tune`
# replaces it so the running feed reflects steering and survives a restart.
_session_profile: dict = load_profile()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """On startup, restore the persisted NL-steered profile override (Loop 3)."""
    global _session_profile
    repo = await _safe_repo()
    if repo is not None:
        try:
            override = await repo.get_profile_override()
            if override:
                _session_profile = override
        except Exception as exc:  # noqa: BLE001 — never block startup on this
            logger.warning("session profile override load failed: %s", exc)
    yield


app = FastAPI(title="ai-digest API", version=VERSION, lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


async def _safe_repo() -> Repo | None:
    """Return a connected Repo, or None if the DB is unreachable (degraded mode)."""
    try:
        return await get_repo()
    except Exception as exc:  # noqa: BLE001 — liveness must never crash on DB outage
        logger.warning("repo unavailable: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@app.get("/api/health")
async def health() -> dict:
    """Liveness + dependency check. Always 200 while the process is up."""
    settings = get_settings()
    repo = await _safe_repo()
    db_status = "ok" if repo is not None else "down"
    return {
        "status": "ok",
        "db": db_status,
        "llm_mock": settings.llm_mock,
        "version": VERSION,
    }


# --------------------------------------------------------------------------- #
# Digests
# --------------------------------------------------------------------------- #


@app.get("/api/digests")
async def list_digests(
    kind: Annotated[DigestKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_DIGEST_LIMIT)] = DEFAULT_DIGEST_LIMIT,
) -> list[dict]:
    """Archive summary rows (newest first). `kind` filters daily|weekly."""
    repo = await _safe_repo()
    if repo is None:
        return []
    return await repo.list_digests(kind=kind, limit=limit)


@app.get("/api/digest/{digest_id}")
async def get_digest(digest_id: str) -> dict:
    """Full DailyDigest|WeeklyDigest by id (serialized). 404 when missing."""
    repo = await _safe_repo()
    if repo is None:
        raise HTTPException(status_code=404, detail="digest not found")
    digest = await repo.get_digest(digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail="digest not found")
    return digest.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Stories
# --------------------------------------------------------------------------- #


@app.get("/api/stories")
async def list_stories(date: Annotated[str | None, Query()] = None) -> list[dict]:
    """Ranked stories for a date (defaults to today, server timezone)."""
    repo = await _safe_repo()
    if repo is None:
        return []
    target_date = date or _today_iso()
    stories = await repo.get_stories_for_date(target_date)
    # Never ship 1536-float embeddings to the UI (API_CONTRACT: embedding == null).
    return [story.model_copy(update={"embedding": None}).model_dump(mode="json") for story in stories]


# --------------------------------------------------------------------------- #
# Feedback (Loop 2)
# --------------------------------------------------------------------------- #


@app.post("/api/feedback", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def post_feedback(body: FeedbackRequest) -> dict:
    """Record a 👍/👎, click, dwell, or NL instruction."""
    repo = await _safe_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="storage unavailable")
    fb = Feedback(
        target_id=body.target_id,
        target_kind=body.target_kind,
        signal=body.signal,
        value=body.value,
        text=body.text,
    )
    stored = await repo.add_feedback(fb)
    return {"ok": True, "id": stored.id}


@app.get("/api/feedback/click", response_class=HTMLResponse)
async def feedback_click(
    target_id: Annotated[str, Query(min_length=1)],
    signal: Annotated[str, Query()],
    target_kind: Annotated[str, Query()] = "story",
    value: Annotated[str, Query()] = "1",
    sig: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Email-feedback shim: a single GET tap records a 👍/👎 from an email client.

    Verifies the HMAC `sig` when link-signing is enabled, then writes feedback and
    returns a tiny confirmation page (email clients cannot POST or send headers).
    """
    if not verify_click_signature(
        target_id=target_id, target_kind=target_kind, signal=signal, value=value, sig=sig
    ):
        raise HTTPException(status_code=403, detail="invalid or missing signature")
    try:
        kind = FeedbackTargetKind(target_kind)
        sig_enum = FeedbackSignal(signal)
        numeric = float(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad feedback param: {exc}") from exc

    repo = await _safe_repo()
    if repo is None:
        return _click_page("Storage is unavailable — please try again later.", ok=False)
    await repo.add_feedback(
        Feedback(target_id=target_id, target_kind=kind, signal=sig_enum, value=numeric)
    )
    verb = "👍 helpful" if signal == "up" else "👎 skip"
    return _click_page(f"Recorded: {verb}. Thanks — your feed just got smarter.", ok=True)


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict:
    """Receive Telegram callback button presses and record them as feedback.

    Validates the shared secret (when configured), decodes `fb:<signal>:<kind>:<id>`,
    persists the feedback, and acks the button so the client stops spinning.
    """
    settings = get_settings()
    secret = settings.telegram_webhook_secret
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail="bad webhook secret")

    parsed = extract_feedback_from_update(update)
    if parsed is None:
        return {"ok": True, "handled": False}
    signal, target_kind, target_id, callback_id = parsed

    repo = await _safe_repo()
    if repo is not None:
        try:
            await repo.add_feedback(
                Feedback(
                    target_id=target_id,
                    target_kind=FeedbackTargetKind(target_kind),
                    signal=FeedbackSignal(signal),
                    value=1.0 if signal == "up" else -1.0,
                )
            )
        except ValueError as exc:
            logger.warning("telegram webhook bad feedback: %s", exc)
    await answer_callback_query(callback_id, text="Thanks!")
    return {"ok": True, "handled": True}


def _click_page(message: str, *, ok: bool) -> HTMLResponse:
    """Minimal Hybrid-Editorial confirmation page for the email click shim."""
    accent = "#8B2E2E" if ok else "#6B6660"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ai-digest</title></head>
<body style="margin:0;background:#FAF8F3;color:#1A1A1A;
 font-family:'Source Serif Pro',Georgia,serif;">
<div style="max-width:480px;margin:80px auto;padding:0 24px;text-align:center;">
<p style="font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:0.08em;
 text-transform:uppercase;color:{accent};">ai-digest</p>
<p style="font-size:20px;line-height:1.5;">{message}</p>
</div></body></html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 503)


# --------------------------------------------------------------------------- #
# Tune (Loop 3)
# --------------------------------------------------------------------------- #


@app.post("/api/tune", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def post_tune(body: TuneRequest) -> dict:
    """Convert a natural-language instruction into an updated session profile.

    The steered profile is persisted (best-effort) so it survives a restart, and
    the NL instruction is recorded as feedback for the audit trail.
    """
    global _session_profile
    updated = await apply_nl_instruction(body.instruction, _session_profile)
    _session_profile = updated

    repo = await _safe_repo()
    if repo is not None:
        try:
            await repo.save_profile_override(updated)
            await repo.add_feedback(
                Feedback(
                    target_id="profile",
                    target_kind=FeedbackTargetKind.DIGEST,
                    signal=FeedbackSignal.NL_INSTRUCTION,
                    value=1.0,
                    text=body.instruction,
                )
            )
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning("tune persistence failed: %s", exc)
    return {"ok": True, "profile": updated}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _today_iso() -> str:
    """Today's date in the configured timezone as an ISO string (YYYY-MM-DD)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(get_settings().timezone)
    return datetime.now(tz).date().isoformat()


__all__ = ["app"]
