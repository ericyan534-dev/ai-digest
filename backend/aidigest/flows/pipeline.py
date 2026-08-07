"""End-to-end pipeline orchestration.

A single place that wires the stages:

    ingest -> embed -> dedup -> cluster -> rank -> enrich -> (importance) ->
    generate.daily / generate.weekly -> repo.save -> (deliver)

Plain async functions — runnable as `python -m scripts.run_daily` with NO
Prefect dependency. If Prefect happens to be installed, `as_prefect_flow()`
can wrap any of these for scheduling, but Prefect is never hard-required.

All LLM access goes through `aidigest.llm.factory.get_llm()`; all repository
access through `aidigest.db.repo.get_repo()`. Runs fully in MOCK mode with no
network when AIDIGEST_LLM_MOCK=1.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from aidigest.config import get_settings
from aidigest.db.repo import Repo, get_repo
from aidigest.deliver.email_resend import send_email
from aidigest.deliver.render_md import (
    render_daily_html,
    render_daily_md,
    render_weekly_html,
    render_weekly_md,
)
from aidigest.deliver.telegram_bot import send_daily as tg_send_daily
from aidigest.deliver.wiki_export import export_daily as wiki_export_daily
from aidigest.deliver.wiki_export import export_weekly as wiki_export_weekly
from aidigest.eval.judge import grade_digest
from aidigest.flows._timing import logger, step
from aidigest.generate.daily import generate_daily
from aidigest.generate.importance import classify_day
from aidigest.generate.weekly import generate_weekly
from aidigest.llm.factory import get_llm
from aidigest.models import DailyDigest, DigestKind, Family, Item, Story, WeeklyDigest
from aidigest.personalize.feedback import (
    feedback_boosts,
    recompute_interest_vector,
)
from aidigest.personalize.profile import load_profile
from aidigest.process._signals import is_aggregator_item
from aidigest.process.cluster import cluster_into_stories
from aidigest.process.curate import curate_stories
from aidigest.process.embed import embed_items
from aidigest.process.enrich import enrich_stories
from aidigest.process.rank import apply_announcement_floor, score_stories

# Default look-back windows. News uses the strict daily window (no stale
# announcements leaking into the wrong day); academia (arXiv / HF papers) uses a
# wider one because a paper's date is its original submission — not a "today"
# event — and HF's daily-trending list is routinely a few days old, so a strict
# daily window empties the research section, especially on weekends when arXiv
# announces nothing new.
DAILY_LOOKBACK = timedelta(hours=36)
ACADEMIA_LOOKBACK = timedelta(hours=96)
WEEKLY_LOOKBACK = timedelta(days=8)

# Per-source cap on the daily embed/cluster pool. Complete-link clustering is O(n^3),
# so an uncapped 96h window (hundreds of arXiv + HN items) makes processing crawl for
# tens of minutes. Capping each source to its most-recent N (high-volume sources like
# arXiv/HN don't crowd out academia/industry) keeps n ~100 and MATCHES the validated
# preview path, so the automation produces the same digest that was validated offline.
DAILY_ITEM_PER_SOURCE_CAP = 20

# A claim (see `Repo.claim_digest_run`) older than this is presumed abandoned by a
# crashed/killed run and must NOT block catch-up (`run_daily_if_missing` /
# `run_weekly_if_missing`) forever.
_CLAIM_TTL = timedelta(minutes=30)


def _in_daily_window(item: Item, now: datetime) -> bool:
    """Family-aware freshness gate: academia gets ACADEMIA_LOOKBACK, the rest the
    strict DAILY_LOOKBACK. Keeps research present without leaking stale news."""
    span = ACADEMIA_LOOKBACK if item.family == Family.ACADEMIA else DAILY_LOOKBACK
    return item.published_at >= now - span


def balanced_pool(items: list[Item], *, per_source: int = DAILY_ITEM_PER_SOURCE_CAP) -> list[Item]:
    """Cap EACH source to its most-recent `per_source` items.

    Bounds the O(n^3) clustering pool and stops a high-volume source (arXiv, HN) from
    crowding academia/industry out. Shared by run_process and scripts.preview_daily so
    the automation and the offline preview cluster the SAME bounded set.

    Aggregator items (smol.ai source, Latent Space AINews mirror URLs) are silently
    removed before capping. They are still ingested and stored, but must not enter the
    story pool: including them inflates mention_count (fake-corroboration) and can turn
    a quiet day into a falsely busy one.
    """
    from collections import defaultdict

    # Drop meta-aggregator items before any per-source cap is applied so both
    # run_process and preview_daily get the filter automatically.
    items = [it for it in items if not is_aggregator_item(it)]

    by_src: dict[str, list[Item]] = defaultdict(list)
    for it in sorted(items, key=lambda x: x.published_at, reverse=True):
        if len(by_src[it.source]) < per_source:
            by_src[it.source].append(it)
    return [it for lst in by_src.values() for it in lst]


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _today_iso() -> str:
    return datetime.now(_tz()).date().isoformat()


def _week_of_iso(date_iso: str | None = None) -> str:
    """ISO date (YYYY-MM-DD) of the Monday starting the week containing date."""
    d = datetime.fromisoformat(date_iso).date() if date_iso else datetime.now(_tz()).date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _weekly_id(week_of: str) -> str:
    """Stable weekly id `weekly-YYYY-Www` from the week-start ISO date."""
    d = datetime.fromisoformat(week_of).date()
    iso_year, iso_week, _ = d.isocalendar()
    return f"weekly-{iso_year}-W{iso_week:02d}"


# --------------------------------------------------------------------------- #
# Stage 1: ingest
# --------------------------------------------------------------------------- #


async def run_ingest(*, since: datetime | None = None) -> int:
    """Ingest from all adapters -> upsert items. Returns #items written."""
    # Local import avoids importing every adapter (and feedparser) for digest-only runs.
    from aidigest.ingest.registry import ingest_all

    # Fetch the WIDER academia window so research papers land in the DB even on a
    # cold start / weekend; news older than DAILY_LOOKBACK is filtered out at process
    # time by `_in_daily_window`.
    since = since or (datetime.now(UTC) - ACADEMIA_LOOKBACK)
    repo = await get_repo()
    async with step("ingest") as s:
        items = await ingest_all(since)
        written = await repo.upsert_items(items)
        s.set(fetched=len(items), written=written, since=since.isoformat())
    return written


# --------------------------------------------------------------------------- #
# Stage 2: process (embed -> dedup -> cluster -> rank -> enrich)
# --------------------------------------------------------------------------- #


async def run_process(*, since: datetime | None = None) -> int:
    """embed -> dedup -> cluster -> rank -> enrich -> upsert_stories. Returns #stories."""
    now = datetime.now(UTC)
    explicit_since = since is not None
    # Load the WIDER window, then apply the family-aware gate (academia kept longer)
    # unless the caller pinned an exact `since` (replay/tests want it verbatim).
    # `window_since` is the resolved daily bound carried to downstream stages.
    window_since = since or (now - DAILY_LOOKBACK)
    load_since = since or (now - ACADEMIA_LOOKBACK)
    repo = await get_repo()
    llm = get_llm()
    profile = await _effective_profile(repo)

    await _embed_pending(repo, llm)

    async with step("load_items") as s:
        items = await repo.get_items_since(load_since)
        if not explicit_since:
            items = [it for it in items if _in_daily_window(it, now)]
            loaded = len(items)
            items = balanced_pool(items)  # bound the O(n^3) clustering pool
            s.set(loaded=loaded, items=len(items))
        else:
            s.set(items=len(items))

    if not items:
        logger.info("step=process status=skip reason=no_items")
        return 0

    items = await _enrich_academia(repo, items)
    stories = _build_stories(items, threshold=_cluster_threshold(profile))
    stories = await _rank_stories(repo, llm, profile, stories, items, window_since)

    async with step("curate") as s:
        items_by_id = {it.id: it for it in items}
        kept = await curate_stories(stories, profile=profile, llm=llm)  # type: ignore[arg-type]
        kept = apply_announcement_floor(kept, items_by_id)  # real announcements register as notable
        s.set(before=len(stories), after=len(kept))
        stories = kept

    async with step("enrich") as s:
        stories = await enrich_stories(stories, items_by_id, llm=llm)
        s.set(stories=len(stories))

    async with step("upsert_stories") as s:
        # REPLACE today's stories rather than accumulate: clear the local day first so
        # a re-process yields exactly the current curated set (no stale survivors from
        # an earlier/buggier run). Skipped for explicit-since replays of past days.
        deleted = 0 if explicit_since else await repo.delete_stories_for_date(_today_iso())
        n = await repo.upsert_stories(stories)
        s.set(deleted=deleted, written=n)
    return len(stories)


async def _embed_pending(repo: Repo, llm: object) -> None:
    """Embed any items still missing a vector, persisting each result."""
    async with step("embed") as s:
        pending = await repo.get_items_without_embedding()
        embedded = await embed_items(pending, llm=llm)  # type: ignore[arg-type]
        for item in embedded:
            if item.embedding is not None:
                await repo.set_item_embedding(item.id, item.embedding)
        s.set(pending=len(pending), embedded=len(embedded))


def _cluster_threshold(profile: dict) -> float:
    """Complete-link clustering threshold (tunable via profile.yaml)."""
    return float((profile.get("processing") or {}).get("cluster_threshold", 0.86))


async def _effective_profile(repo: Repo) -> dict:
    """Load profile.yaml, then overlay the NL-steered override (Loop 3) when present.

    Only the steerable keys (ranking weights, mutes, subfields) are overlaid so a
    later edit to profile.yaml still shows through for everything else.
    """
    profile = load_profile()
    try:
        override = await repo.get_profile_override()
    except Exception as exc:  # noqa: BLE001 — steering is optional, never fatal
        logger.warning("step=profile override-load-failed: %s", exc)
        override = None
    if not override:
        return profile
    merged = dict(profile)
    for key in ("ranking", "mutes", "subfields"):
        if key in override:
            merged[key] = override[key]
    return merged


async def _load_interest_vector(repo: Repo, profile: dict, llm: object) -> list[float]:
    """Prefer the persisted (nightly-recomputed) interest vector; else recompute.

    Persisting + reading makes the nightly Loop-2 recompute meaningful across runs
    and gives a warm start; cold start (no stored vector) recomputes from feedback.
    """
    try:
        stored = await repo.get_interest_vector()
    except Exception as exc:  # noqa: BLE001 — fall back to a fresh recompute
        logger.warning("step=rank interest-load-failed: %s", exc)
        stored = None
    dim = get_settings().embed_dim
    if stored and len(stored) == dim:
        return stored
    return await recompute_interest_vector(repo, profile, llm=llm)  # type: ignore[arg-type]


async def _enrich_academia(repo: Repo, items: list[Item]) -> list[Item]:
    """Enrich academia items with Semantic Scholar citation velocity, persist, merge.

    Skipped in mock/offline mode and when disabled. Best-effort: failures leave the
    original items untouched. Citation velocity is a core "this paper matters" rank
    signal (design §3.2/§5.4) — without this it is always zero.
    """
    settings = get_settings()
    if not settings.enrich_academia or settings.llm_mock:
        return items
    academia = [it for it in items if it.family == Family.ACADEMIA]
    if not academia:
        return items
    from aidigest.ingest.semantic_scholar import enrich_items

    async with step("enrich_academia") as s:
        enriched = await enrich_items(academia)
        by_id = {it.id: it for it in enriched}
        await repo.upsert_items([it for it in enriched if it.id in by_id])
        merged = [by_id.get(it.id, it) for it in items]
        s.set(academia=len(academia))
    return merged


def _build_stories(items: list[Item], *, threshold: float = 0.86) -> list[Story]:
    """Group items into stories with one complete-link pass.

    Complete-link clustering both collapses cross-source duplicates (preserving
    mention_count — the breakthrough signal) AND keeps distinct topics apart
    without single-link chaining. Replaces the old dedup->representatives->cluster
    two-step, which discarded the cross-source count.
    """
    return cluster_into_stories(items, threshold=threshold)


async def _rank_stories(
    repo: Repo,
    llm: object,
    profile: dict,
    stories: list[Story],
    items: list[Item],  # noqa: ARG001 — kept for signature symmetry / future use
    since: datetime,
) -> list[Story]:
    """Compute the interest vector + feedback boosts, then score/sort stories."""
    async with step("rank") as s:
        interest = await _load_interest_vector(repo, profile, llm)
        feedback = await repo.get_feedback(since=since - timedelta(days=30))
        boosts = feedback_boosts(feedback)
        ranked = score_stories(
            stories,
            interest_vector=interest,
            profile=profile,
            feedback_boost=boosts,
        )
        s.set(stories=len(ranked), feedback=len(feedback))
    return ranked


# --------------------------------------------------------------------------- #
# Stage 3a: daily
# --------------------------------------------------------------------------- #


async def run_daily(*, date: str | None = None, deliver: bool = False) -> DailyDigest:
    """Full daily: ensure stories, classify tiers, generate, save, optionally deliver."""
    date = date or _today_iso()
    repo = await get_repo()
    llm = get_llm()
    profile = await _effective_profile(repo)

    async with step("claim") as s:
        await repo.claim_digest_run(DigestKind.DAILY, date)
        s.set(date=date)

    stories, items_by_id = await _stories_for_date(repo, date)

    async with step("classify_day") as s:
        stories, overall_tier, quiet = classify_day(stories, profile=profile)
        s.set(stories=len(stories), tier=overall_tier.value, quiet=quiet)

    async with step("persist_tiers") as s:
        n = await repo.upsert_stories(stories)
        s.set(stories=n)

    async with step("generate_daily") as s:
        digest = await generate_daily(
            stories, items_by_id, profile=profile, date=date, llm=llm
        )
        s.set(sections=len(digest.sections), tier=digest.overall_tier.value)

    async with step("save_daily"):
        await repo.save_daily(digest)

    if deliver:
        await _deliver_daily(digest, repo=repo)
    return digest


async def _stories_for_date(repo: Repo, date: str) -> tuple[list[Story], dict[str, Item]]:
    """Load (or build) the day's stories + the items that back them.

    For TODAY this ALWAYS re-processes: run_process replaces today's stories, so the
    digest reflects the latest items + the current (fixed) pipeline and never serves a
    stale set cached by an earlier run. Past-date replays use whatever is stored.
    """
    async with step("load_stories") as s:
        curated = 0
        if date == _today_iso():
            logger.info("step=load_stories action=reprocess_today")
            curated = await run_process()
        stories = await repo.get_stories_for_date(date)
        # CARRY-OVER (intentional): story ids are content-derived and stable, and
        # upsert_stories leaves created_at alone on conflict, while stories are
        # bucketed by created_at. So a story the curator picks again today but that
        # first appeared earlier stays on its original day and does NOT repeat here.
        # That is the cross-day dedup we want; it is logged because it makes the
        # curated count and the digest count differ, which otherwise looks like loss.
        if curated:
            s.set(carried_over=max(0, curated - len(stories)))
        if not stories and date != _today_iso():
            logger.info("step=load_stories status=empty action=run_process")
            await run_process()
            stories = await repo.get_stories_for_date(date)
        item_ids = sorted({iid for st in stories for iid in st.item_ids})
        items = await repo.get_items_by_ids(item_ids)
        s.set(stories=len(stories), items=len(items))
    return stories, {it.id: it for it in items}


async def _deliver_daily(digest: DailyDigest, *, repo: Repo) -> None:
    """Best-effort delivery; channels self-disable when unconfigured.

    Records the outcome via `repo.record_delivery` so `run_daily_if_missing` can
    tell "generated but never delivered" (the partial-failure case) apart from a
    digest that actually shipped. When NEITHER channel is configured, `delivered`
    is honestly True — there was nothing to deliver — but that fact is logged as
    `channels=none` so it stays visible in the run log.
    """
    async with step("deliver_daily") as s:
        settings = get_settings()
        html = render_daily_html(
            digest,
            api_base=settings.public_base_url,
            link_secret=settings.feedback_link_secret,
        )
        emailed = await send_email(
            subject=f"AI Digest — {digest.date}", html=html, text=render_daily_md(digest)
        )
        telegrammed = await tg_send_daily(digest)
        wiki_n = (
            len(wiki_export_daily(digest, wiki_dir=settings.wiki_dir)) if settings.wiki_dir else 0
        )

        channels_configured = settings.email_enabled or settings.telegram_enabled
        delivered = emailed or telegrammed or not channels_configured
        await repo.record_delivery(
            DigestKind.DAILY, digest.date, email=emailed, telegram=telegrammed, delivered=delivered
        )

        extra: dict[str, object] = {
            "email": emailed,
            "telegram": telegrammed,
            "wiki": wiki_n,
            "delivered": delivered,
        }
        if not channels_configured:
            extra["channels"] = "none"
        s.set(**extra)


# --------------------------------------------------------------------------- #
# Stage 3b: weekly
# --------------------------------------------------------------------------- #


async def run_weekly(*, week_of: str | None = None, deliver: bool = False) -> WeeklyDigest:
    """Full weekly: gather the week's stories, best-of-N editorial, save, deliver."""
    week_of = _week_of_iso(week_of)
    repo = await get_repo()
    llm = get_llm()
    profile = await _effective_profile(repo)

    async with step("claim") as s:
        await repo.claim_digest_run(DigestKind.WEEKLY, week_of)
        s.set(week_of=week_of)

    async with step("load_week") as s:
        stories = await _week_stories(repo, week_of)
        item_ids = sorted({iid for st in stories for iid in st.item_ids})
        items = await repo.get_items_by_ids(item_ids)
        items_by_id = {it.id: it for it in items}
        feedback = await repo.get_feedback(since=datetime.now(UTC) - WEEKLY_LOOKBACK)
        s.set(stories=len(stories), items=len(items), feedback=len(feedback))

    async with step("generate_weekly") as s:
        digest = await generate_weekly(
            stories,
            items_by_id,
            profile=profile,
            week_of=week_of,
            feedback=feedback,
            llm=llm,
        )
        # body_chars/shortlist/radar make a blank editorial visible in the run log
        # instead of silently shipping a header over empty space.
        s.set(
            candidates=digest.candidate_count,
            winner=digest.winning_candidate,
            body_chars=len(digest.body_markdown),
            shortlist=len(digest.shortlist),
            radar=len(digest.on_my_radar),
        )

    async with step("save_weekly"):
        await repo.save_weekly(digest)

    if deliver:
        await _deliver_weekly(digest, repo=repo)
    return digest


async def _week_stories(repo: Repo, week_of: str) -> list[Story]:
    """Union of the seven daily story sets in the week starting `week_of`."""
    start = datetime.fromisoformat(week_of).date()
    seen: dict[str, Story] = {}
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        for st in await repo.get_stories_for_date(day):
            seen.setdefault(st.id, st)
    if not seen:
        # No stories yet for the week — process now so the editorial has material.
        await run_process()
        for st in await repo.get_stories_for_date(start.isoformat()):
            seen.setdefault(st.id, st)
    return list(seen.values())


def _week_dates(week_of: str) -> list[str]:
    """The seven ISO dates of the week starting `week_of` (Monday)."""
    start = datetime.fromisoformat(week_of).date()
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


async def _deliver_weekly(digest: WeeklyDigest, *, repo: Repo) -> None:
    """Best-effort delivery (email only — weekly has no Telegram channel).

    Records the outcome via `repo.record_delivery`; see `_deliver_daily` for the
    `delivered`/`channels=none` semantics, mirrored here with `telegram=False`.
    """
    async with step("deliver_weekly") as s:
        settings = get_settings()
        html = render_weekly_html(digest)
        # Parity with _deliver_daily: the plain-text part is the FULL rendered
        # digest, not just the body — otherwise text-only clients lose the
        # shortlist and the radar entirely.
        emailed = await send_email(
            subject=digest.title or "AI Digest — Week",
            html=html,
            text=render_weekly_md(digest),
        )
        wiki_n = (
            len(
                wiki_export_weekly(
                    digest, wiki_dir=settings.wiki_dir, daily_dates=_week_dates(digest.week_of)
                )
            )
            if settings.wiki_dir
            else 0
        )

        channels_configured = settings.email_enabled  # weekly has no telegram channel
        delivered = emailed or not channels_configured
        await repo.record_delivery(
            DigestKind.WEEKLY, digest.week_of, email=emailed, telegram=False, delivered=delivered
        )

        extra: dict[str, object] = {"email": emailed, "wiki": wiki_n, "delivered": delivered}
        if not channels_configured:
            extra["channels"] = "none"
        s.set(**extra)


# --------------------------------------------------------------------------- #
# Stage 3c: catch-up — self-heal a slot GitHub Actions failed to run at all
# --------------------------------------------------------------------------- #
#
# 2026-08-06 incident: two scheduled runs failed with "job was not acquired by
# Runner" — the job never started, so nothing ran, retried, or alerted. These
# entrypoints are meant to be scheduled a few hours AFTER the primary daily/weekly
# slot; they are no-ops when that slot's digest already shipped.

async def _catch_up[DigestT: (DailyDigest, WeeklyDigest)](
    repo: Repo,
    *,
    kind: DigestKind,
    digest_id: str,
    date: str,
    deliver: bool,
    run: Callable[[], Awaitable[DigestT]],
) -> DigestT | None:
    """Shared run_daily_if_missing / run_weekly_if_missing semantics.

    Checked in this order (already-delivered BEFORE claiming, so a run must not
    burn its own claim before that check — and so a completed run is never
    blocked by its own now-irrelevant claim history):

    1. ALREADY DONE — a stored digest exists for `digest_id` AND (`deliver` is
       False, meaning delivery was never asked for, OR a delivery record exists
       for `kind` whose date matches and whose `delivered` flag is true). Returns
       the stored digest.
    2. COULD NOT ACQUIRE THE CLAIM — `repo.try_claim_digest_run` is an atomic
       compare-and-set (see its docstring): it returns False exactly when
       another run already holds a fresh (younger than `_CLAIM_TTL`) claim for
       this same date, i.e. a run is genuinely in flight right now. Returns
       None. The TTL bound means a crashed run's stale claim stops blocking
       catch-up after 30 minutes — it can never wedge catch-up forever.
    3. Otherwise — this call just won the claim, so it delegates to `run` (the
       real run_daily/run_weekly) and returns its result; this is the self-heal
       path for the missed-slot incident.
    """
    stored = await repo.get_digest(digest_id)
    if stored is not None and (deliver is False or await _delivery_matches(repo, kind, date)):
        logger.info(
            "step=catch_up kind=%s status=skip reason=already_delivered date=%s",
            kind.value,
            date,
        )
        return cast(DigestT, stored)

    if not await repo.try_claim_digest_run(kind, date, ttl=_CLAIM_TTL):
        logger.info("step=catch_up kind=%s status=skip reason=in_flight date=%s", kind.value, date)
        return None

    return await run()


async def _delivery_matches(repo: Repo, kind: DigestKind, date: str) -> bool:
    delivery = await repo.get_delivery(kind)
    return bool(delivery and delivery.get("date") == date and delivery.get("delivered"))


async def run_daily_if_missing(
    *, date: str | None = None, deliver: bool = False
) -> DailyDigest | None:
    """Catch-up entrypoint: run the daily digest only if `date` has not already
    shipped and no run for it is currently in flight. See `_catch_up`.
    """
    date = date or _today_iso()
    repo = await get_repo()
    return await _catch_up(
        repo,
        kind=DigestKind.DAILY,
        digest_id=f"daily-{date}",
        date=date,
        deliver=deliver,
        run=lambda: run_daily(date=date, deliver=deliver),
    )


async def run_weekly_if_missing(
    *, week_of: str | None = None, deliver: bool = False
) -> WeeklyDigest | None:
    """Catch-up entrypoint: run the weekly digest only if `week_of` has not already
    shipped and no run for it is currently in flight. See `_catch_up`.
    """
    week_of = _week_of_iso(week_of)
    repo = await get_repo()
    return await _catch_up(
        repo,
        kind=DigestKind.WEEKLY,
        digest_id=_weekly_id(week_of),
        date=week_of,
        deliver=deliver,
        run=lambda: run_weekly(week_of=week_of, deliver=deliver),
    )


# --------------------------------------------------------------------------- #
# Stage 4: nightly maintenance
# --------------------------------------------------------------------------- #


async def run_nightly() -> None:
    """Recompute the interest vector (Loop 2) + grade the latest daily digest."""
    repo = await get_repo()
    llm = get_llm()
    profile = await _effective_profile(repo)

    async with step("recompute_interest") as s:
        vector = await recompute_interest_vector(repo, profile, llm=llm)
        # Persist so the NEXT day's ranking reads this vector (the nightly run is
        # the writer; run_process is the reader). Closes Loop 2 across runs.
        await repo.save_interest_vector(vector)
        s.set(dim=len(vector), persisted=True)

    async with step("eval_latest_daily") as s:
        rows = await repo.list_digests(kind=None, limit=30)
        daily = next((r for r in rows if r.get("kind") == "daily"), None)
        if daily is None:
            s.set(graded=False, reason="no_daily")
            return
        digest = await repo.get_digest(daily["id"])
        if not isinstance(digest, DailyDigest):
            s.set(graded=False, reason="not_daily")
            return
        markdown = render_daily_md(digest)
        result = await grade_digest(
            markdown, quiet_expected=digest.quiet_day, llm=llm
        )
        await repo.save_eval_run(
            digest_id=digest.id,
            judge_model=getattr(llm, "model", ""),
            scores=result.get("scores", {}),
            notes=result.get("notes"),
        )
        s.set(graded=True, digest=digest.id, total=result.get("total"))


# --------------------------------------------------------------------------- #
# Prefect-optional wrapper
# --------------------------------------------------------------------------- #


def as_prefect_flow(fn: object) -> object:
    """Wrap `fn` as a Prefect flow IF Prefect is installed; else return it as-is.

    Lets the same plain async functions be scheduled by Prefect without making
    Prefect a hard dependency (it is intentionally NOT in requirements.txt).
    """
    try:
        from prefect import flow  # type: ignore[import-not-found]
    except ImportError:
        return fn
    return flow(fn)  # type: ignore[arg-type]


__all__ = [
    "run_ingest",
    "run_process",
    "run_daily",
    "run_daily_if_missing",
    "run_weekly",
    "run_weekly_if_missing",
    "run_nightly",
    "as_prefect_flow",
]
