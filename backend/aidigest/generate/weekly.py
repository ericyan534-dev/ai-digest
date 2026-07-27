"""Weekly 'Week at a Glance' generation: best-of-N + LLM-as-judge + polish.

Pipeline:
  1. Generate ``n_candidates`` editorial drafts, each with a different LEAD ANGLE
     so the judge has real variety to choose from.
  2. Judge the candidates against the editorial rubric (``eval.judge`` when
     available, else the LLM's own ``judge``), pick the winner.
  3. Polish the winning draft (tighten prose; do not add facts).

The editorial is NYT-style narrative for one expert reader, honoring the active
:class:`ImportanceTier` for the week (full depth for breakthroughs, honest
"quiet week" handling otherwise). Includes a "What I'd actually read this week"
shortlist and an "On my radar" academia preview.

``id = f'weekly-{ISO-week}'`` derived from ``week_of``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_cls

from aidigest.eval.rubric import rubric
from aidigest.generate._shared import (
    parse_json_obj,
    sources_block,
    story_items,
    subfields_str,
    venues_str,
)
from aidigest.generate.importance import classify_day
from aidigest.generate.prompts import (
    WEEKLY_CANDIDATE,
    WEEKLY_POLISH,
    load_prompt,
    voice_prompt,
)
from aidigest.llm.base import LLMClient, Message
from aidigest.llm.factory import get_judge_llm, get_llm
from aidigest.models import (
    DigestKind,
    Family,
    Feedback,
    ImportanceTier,
    Item,
    Story,
    WeeklyDigest,
    WeeklyShortlistEntry,
)

logger = logging.getLogger("aidigest.generate")

# Modest headroom for the longest generation in the system — NOT a fix for the
# blank digest on its own. A healthy weekly fits inside the default 8192 (the
# 2026-07-12 run did, with room to spare); the failures were a runaway string
# field, which is bounded in _CANDIDATE_SCHEMA above. Measured against the real
# API: 8192 and 16384 both truncated a runaway, and 32768 did not return at all
# (the request outran http_timeout_seconds and the server disconnected). So more
# budget buys a legitimately rich week some room while staying well clear of the
# request timeout — it does not, and cannot, stop a runaway.
_LONG_FORM_MAX_OUTPUT_TOKENS = 16384

# How many stories the deterministic fallback body lists when the LLM returns
# nothing usable.
_FALLBACK_STORY_LIMIT = 15

# Distinct lead angles so each candidate opens differently (best-of-N variety).
_LEAD_ANGLES: list[str] = [
    "Open on the single most important thing that happened this week and use it "
    "as the spine; everything else is context around it.",
    "Open on the through-line connecting several stories — the theme nobody named "
    "— and let the individual items hang off that thread.",
    "Open on the contrast/tension of the week (e.g. academia vs industry, hype vs "
    "what shipped) and adjudicate it plainly.",
    "Open on what was conspicuously ABSENT or quiet, then pivot to what did move.",
]

# BOUNDED BY CONSTRUCTION. An unconstrained string field lets the model run away:
# on 2026-07-19 the `title` swallowed the rest of the JSON object (the rendered
# headline contained `", "lede": "...", "body_markdown": ...`), and on 2026-07-26
# it degenerated into a repetition loop that burned the entire output budget
# before `body_markdown` was ever reached, so nothing parsed and the digest
# shipped blank. Lengths cap the runaway, `required` forces the body to exist,
# `propertyOrdering` pins title/lede/body ahead of the arrays, and the
# descriptions tell the model what each field is FOR.
_CANDIDATE_SCHEMA: dict = {
    "type": "object",
    "required": ["title", "lede", "body_markdown"],
    "propertyOrdering": ["title", "lede", "body_markdown", "shortlist", "on_my_radar"],
    "properties": {
        "title": {
            "type": "string",
            "maxLength": 200,
            "description": (
                "One editorial headline, under 15 words. Plain prose — not JSON, "
                "not a list, not a slug."
            ),
        },
        "lede": {
            "type": "string",
            "maxLength": 800,
            "description": "One or two sentences of narrative opening.",
        },
        "body_markdown": {
            "type": "string",
            "description": "The full editorial, in markdown.",
        },
        "shortlist": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 300},
                    "url": {"type": "string", "maxLength": 500},
                    "one_liner": {"type": "string", "maxLength": 400},
                    "family": {
                        "type": "string",
                        "enum": [f.value for f in Family],
                    },
                },
            },
        },
        "on_my_radar": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 300},
                    "url": {"type": "string", "maxLength": 500},
                    "one_liner": {"type": "string", "maxLength": 400},
                    "family": {
                        "type": "string",
                        "enum": [f.value for f in Family],
                    },
                },
            },
        },
    },
}


def _iso_week_id(week_of: str) -> str:
    """Derive ``weekly-YYYY-Www`` from an ISO date string; fallback-safe."""
    try:
        d = date_cls.fromisoformat(week_of)
        iso = d.isocalendar()
        return f"weekly-{iso.year}-W{iso.week:02d}"
    except (ValueError, TypeError):
        return f"weekly-{week_of}"


def _story_blocks(stories: list[Story], items_by_id: dict[str, Item], *, limit: int = 20) -> str:
    """Render ranked stories (with tier tags) into a prompt block."""
    blocks: list[str] = []
    for story in stories[:limit]:
        blocks.append(
            f"### [{story.tier.value}] {story.title} "
            f"(family={story.family.value}, mentions={story.mention_count})\n"
            f"{sources_block(story, items_by_id)}"
        )
    return "\n\n".join(blocks) if blocks else "(no stories this week)"


async def _generate_candidate(
    *,
    index: int,
    n_candidates: int,
    stories: list[Story],
    items_by_id: dict[str, Item],
    profile: dict,
    week_of: str,
    overall_tier: ImportanceTier,
    quiet_week: bool,
    llm: LLMClient,
) -> str:
    """Generate one editorial candidate (raw JSON string).

    Truncation is logged rather than raised: a partial candidate can still lose
    the judge vote to an intact sibling, and the caller has its own fallback.
    """
    angle = _LEAD_ANGLES[index % len(_LEAD_ANGLES)]
    prompt_body = load_prompt(WEEKLY_CANDIDATE).format(
        candidate_index=index + 1,
        n_candidates=n_candidates,
        lead_angle=angle,
        week_of=week_of,
        overall_tier=overall_tier.value,
        quiet_week=quiet_week,
        subfields=subfields_str(profile),
        venues=venues_str(profile),
        story_blocks=_story_blocks(stories, items_by_id),
    )
    messages = [
        Message(role="system", content=voice_prompt()),
        Message(role="user", content=prompt_body),
    ]
    # Slight temperature spread broadens candidate diversity.
    temperature = 0.6 + 0.1 * (index % 3)
    result = await llm.generate_detailed(
        messages,
        json_schema=_CANDIDATE_SCHEMA,
        temperature=temperature,
        max_output_tokens=_LONG_FORM_MAX_OUTPUT_TOKENS,
    )
    if result.truncated:
        logger.warning(
            "weekly candidate %d/%d truncated at %d output tokens",
            index + 1,
            n_candidates,
            _LONG_FORM_MAX_OUTPUT_TOKENS,
        )
    return result.text


async def _judge(candidates: list[str], *, context: str, llm: LLMClient) -> dict:
    """Judge candidates via eval.judge when available, else the LLM directly."""
    try:
        from aidigest.eval.judge import judge_candidates

        return await judge_candidates(candidates, context=context, llm=llm)
    except ImportError:
        return await llm.judge(candidates=candidates, rubric=rubric(), context=context)


async def _polish(
    *, winning_raw: str, n_candidates: int, rationale: str, llm: LLMClient
) -> str:
    """Run the polish pass over the winning draft; return raw JSON string."""
    prompt_body = load_prompt(WEEKLY_POLISH).format(
        n_candidates=n_candidates,
        judge_rationale=rationale or "(no rationale provided)",
        winning_draft=winning_raw,
    )
    messages = [
        Message(role="system", content=voice_prompt()),
        Message(role="user", content=prompt_body),
    ]
    result = await llm.generate_detailed(
        messages,
        json_schema=_CANDIDATE_SCHEMA,
        temperature=0.3,
        max_output_tokens=_LONG_FORM_MAX_OUTPUT_TOKENS,
    )
    if result.truncated:
        logger.warning(
            "weekly polish truncated at %d output tokens", _LONG_FORM_MAX_OUTPUT_TOKENS
        )
    return result.text


def _parse_entries(
    raw_list: object, valid_urls: frozenset[str] | None = None
) -> list[WeeklyShortlistEntry]:
    """Build WeeklyShortlistEntry list from parsed JSON, skipping bad rows.

    GROUNDING: a shortlist URL is kept only if it is a REAL story link (present in
    ``valid_urls``); an invented/mis-attributed URL is nulled so we never link the
    reader to a fabricated source. ``None`` disables the check (callers that hold
    no source list); an EMPTY set means "we checked and know of no valid URL", so
    every link is nulled rather than waved through.
    """
    entries: list[WeeklyShortlistEntry] = []
    if not isinstance(raw_list, list):
        return entries
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        family = _coerce_family(row.get("family"))
        raw_url = str(row.get("url") or "").strip() or None
        url = raw_url if (raw_url and (valid_urls is None or raw_url in valid_urls)) else None
        entries.append(
            WeeklyShortlistEntry(
                title=title, url=url, one_liner=str(row.get("one_liner") or "").strip(),
                family=family,
            )
        )
    return entries


def _coerce_family(value: object) -> Family:
    try:
        return Family(str(value))
    except (ValueError, TypeError):
        return Family.META


async def generate_weekly(
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    profile: dict,
    week_of: str,
    feedback: list[Feedback] | None = None,
    llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    n_candidates: int = 3,
) -> WeeklyDigest:
    """Generate the weekly editorial via best-of-N + judge + polish.

    ``feedback`` is accepted for signature parity (future re-ranking hook) and is
    not required for generation. Honest quiet-week handling is driven by the tier
    classification and the prompt. ``judge_llm`` is an INDEPENDENT judge client
    (design §7.2) — defaults to ``get_judge_llm()`` so the best-of-N winner is not
    self-graded by the same client instance.
    """
    client = llm or get_llm()
    judge_client = judge_llm or get_judge_llm()
    n = max(1, n_candidates)

    tagged, overall_tier, quiet_week = classify_day(stories, profile=profile)

    # 1. Best-of-N candidate drafts (concurrent). One transport failure must not
    # take the whole week down with it — that is the point of generating N.
    settled = await asyncio.gather(
        *(
            _generate_candidate(
                index=i,
                n_candidates=n,
                stories=tagged,
                items_by_id=items_by_id,
                profile=profile,
                week_of=week_of,
                overall_tier=overall_tier,
                quiet_week=quiet_week,
                llm=client,
            )
            for i in range(n)
        ),
        return_exceptions=True,
    )
    candidates: list[str] = []
    for index, outcome in enumerate(settled):
        if isinstance(outcome, BaseException):
            logger.warning(
                "weekly candidate %d/%d failed: %s", index + 1, n, type(outcome).__name__
            )
            continue
        candidates.append(outcome)

    winner_idx = 0
    rationale = ""
    eval_scores: dict = {}
    polished_raw = ""
    if candidates:
        # 2. Judge -> winner (independent judge client).
        context = (
            f"Weekly digest for {week_of}. Overall tier: {overall_tier.value}. "
            f"Quiet week: {quiet_week}."
        )
        verdict = await _judge(candidates, context=context, llm=judge_client)
        winner_idx = int(verdict.get("winner", 0))
        if not 0 <= winner_idx < len(candidates):
            winner_idx = 0
        rationale = str(verdict.get("rationale") or "")
        eval_scores = _winner_scores(verdict, winner_idx)

        # 3. Polish the winner.
        polished_raw = await _polish(
            winning_raw=candidates[winner_idx],
            n_candidates=n,
            rationale=rationale,
            llm=client,
        )
    else:
        logger.error("weekly: every one of %d candidate generations failed", n)

    # Prefer the polished draft, then the judge's winner, then ANY intact candidate.
    # Truncated JSON parses to {} (or to an object with no body), so with best-of-N
    # already in hand there is no reason to ship nothing while a sibling draft is
    # whole.
    drafts = [polished_raw, *candidates[winner_idx : winner_idx + 1], *candidates]
    parsed = _first_usable(drafts, n_candidates=n)

    title = str(parsed.get("title") or "").strip() or f"Week at a Glance — {week_of}"
    lede = str(parsed.get("lede") or "").strip()
    body = str(parsed.get("body_markdown") or "").strip()
    if not body:
        # NEVER ship an empty editorial. A quiet week says so honestly; otherwise
        # the LLM failed us and the reader still gets the week's real material
        # rather than a header over blank space (observed 2026-07-26).
        body = (
            "Quiet week — nothing major shipped."
            if quiet_week
            else _fallback_body(tagged, items_by_id)
        )

    # GROUNDING: only allow shortlist/radar links that point at a REAL story source.
    valid_urls = frozenset(
        it.url for it in items_by_id.values() if it.url
    )

    return WeeklyDigest(
        id=_iso_week_id(week_of),
        kind=DigestKind.WEEKLY,
        week_of=week_of,
        title=title,
        lede=lede,
        body_markdown=body,
        overall_tier=overall_tier,
        quiet_week=quiet_week,
        shortlist=_parse_entries(parsed.get("shortlist"), valid_urls),
        on_my_radar=_parse_entries(parsed.get("on_my_radar"), valid_urls),
        story_ids=[s.id for s in tagged],
        candidate_count=n,
        winning_candidate=winner_idx,
        model=getattr(client, "model", ""),
        judge_model=getattr(judge_client, "model", ""),
        eval_scores=eval_scores,
    )


def _first_usable(raw_drafts: list[str], *, n_candidates: int) -> dict:
    """First draft that parses to an object with a non-empty ``body_markdown``.

    Falls back to the first draft that merely parses, then to ``{}`` — the caller
    supplies a grounded body for that last case. Logs the degradation so a blank
    or near-blank weekly is visible in the run log instead of silent.
    """
    parsed_any: dict = {}
    for index, raw in enumerate(raw_drafts):
        parsed = parse_json_obj(raw)
        if not parsed:
            continue
        if not parsed_any:
            parsed_any = parsed
        if str(parsed.get("body_markdown") or "").strip():
            if index > 0:
                logger.warning(
                    "weekly: draft %d of the polish/candidate chain was unusable; "
                    "fell through to a later draft",
                    index,
                )
            return parsed
    if parsed_any:
        logger.error("weekly: no draft carried a body; using a partially parsed draft")
        return parsed_any
    logger.error(
        "weekly: no parseable JSON from %d candidates + polish (likely MAX_TOKENS "
        "truncation); falling back to the deterministic story list",
        n_candidates,
    )
    return {}


def _fallback_body(
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    limit: int = _FALLBACK_STORY_LIMIT,
) -> str:
    """A deterministic, grounded stand-in body for when the LLM returns nothing.

    Honest by construction: it says the editorial pass failed instead of dressing
    a raw list up as writing, and every line comes from a real story (no invented
    facts, links only to source URLs we actually hold).
    """
    if not stories:
        return "Quiet week — nothing major shipped."

    lines = [
        "_The editorial pass failed to return usable copy this week. "
        "Below is the week's ranked material, unedited._",
        "",
    ]
    for story in stories[:limit]:
        title = (story.title or "").strip()
        if not title:
            continue
        members = story_items(story, items_by_id)
        url = next((it.url for it in members if it.url), None)
        headline = f"[{title}]({url})" if url else title
        lines.append(f"- **{headline}** — {story.family.value}, {story.mention_count} mention(s)")
    return "\n".join(lines)


def _winner_scores(verdict: dict, winner_idx: int) -> dict:
    """Extract the winning candidate's per-criterion scores from a judge verdict."""
    scores = verdict.get("scores")
    if isinstance(scores, list) and 0 <= winner_idx < len(scores):
        row = scores[winner_idx]
        if isinstance(row, dict):
            return dict(row)
    return {}


__all__ = ["generate_weekly"]
