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

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aidigest.config import get_settings
from aidigest.db.repo import Repo, get_repo
from aidigest.deliver.email_resend import send_email
from aidigest.deliver.render_md import (
    render_daily_html,
    render_daily_md,
    render_weekly_html,
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
from aidigest.models import DailyDigest, Family, Item, Story, WeeklyDigest
from aidigest.personalize.feedback import (
    feedback_boosts,
    recompute_interest_vector,
)
from aidigest.personalize.profile import load_profile
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
    """
    from collections import defaultdict

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
        kept = await curate_stories(stories, profile=profile, llm=llm)  # type: ignore[arg-type]
        kept = apply_announcement_floor(kept)  # real announcements register as notable
        s.set(before=len(stories), after=len(kept))
        stories = kept

    async with step("enrich") as s:
        items_by_id = {it.id: it for it in items}
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

    stories, items_by_id = await _stories_for_date(repo, date)

    async with step("classify_day") as s:
        stories, overall_tier, quiet = classify_day(stories, profile=profile)
        s.set(stories=len(stories), tier=overall_tier.value, quiet=quiet)

    async with step("generate_daily") as s:
        digest = await generate_daily(
            stories, items_by_id, profile=profile, date=date, llm=llm
        )
        s.set(sections=len(digest.sections), tier=digest.overall_tier.value)

    async with step("save_daily"):
        await repo.save_daily(digest)

    if deliver:
        await _deliver_daily(digest)
    return digest


async def _stories_for_date(repo: Repo, date: str) -> tuple[list[Story], dict[str, Item]]:
    """Load (or build) the day's stories + the items that back them.

    For TODAY this ALWAYS re-processes: run_process replaces today's stories, so the
    digest reflects the latest items + the current (fixed) pipeline and never serves a
    stale set cached by an earlier run. Past-date replays use whatever is stored.
    """
    async with step("load_stories") as s:
        if date == _today_iso():
            logger.info("step=load_stories action=reprocess_today")
            await run_process()
        stories = await repo.get_stories_for_date(date)
        if not stories and date != _today_iso():
            logger.info("step=load_stories status=empty action=run_process")
            await run_process()
            stories = await repo.get_stories_for_date(date)
        item_ids = sorted({iid for st in stories for iid in st.item_ids})
        items = await repo.get_items_by_ids(item_ids)
        s.set(stories=len(stories), items=len(items))
    return stories, {it.id: it for it in items}


async def _deliver_daily(digest: DailyDigest) -> None:
    """Best-effort delivery; channels self-disable when unconfigured."""
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
        wiki_dir = get_settings().wiki_dir
        wiki_n = len(wiki_export_daily(digest, wiki_dir=wiki_dir)) if wiki_dir else 0
        s.set(email=emailed, telegram=telegrammed, wiki=wiki_n)


# --------------------------------------------------------------------------- #
# Stage 3b: weekly
# --------------------------------------------------------------------------- #


async def run_weekly(*, week_of: str | None = None, deliver: bool = False) -> WeeklyDigest:
    """Full weekly: gather the week's stories, best-of-N editorial, save, deliver."""
    week_of = _week_of_iso(week_of)
    repo = await get_repo()
    llm = get_llm()
    profile = await _effective_profile(repo)

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
        s.set(candidates=digest.candidate_count, winner=digest.winning_candidate)

    async with step("save_weekly"):
        await repo.save_weekly(digest)

    if deliver:
        await _deliver_weekly(digest)
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


async def _deliver_weekly(digest: WeeklyDigest) -> None:
    async with step("deliver_weekly") as s:
        html = render_weekly_html(digest)
        emailed = await send_email(
            subject=digest.title or "AI Digest — Week",
            html=html,
            text=digest.body_markdown or None,
        )
        wiki_dir = get_settings().wiki_dir
        wiki_n = (
            len(
                wiki_export_weekly(
                    digest, wiki_dir=wiki_dir, daily_dates=_week_dates(digest.week_of)
                )
            )
            if wiki_dir
            else 0
        )
        s.set(email=emailed, wiki=wiki_n)


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
    "run_weekly",
    "run_nightly",
    "as_prefect_flow",
]
