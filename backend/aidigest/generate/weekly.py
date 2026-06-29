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
from datetime import date as date_cls

from aidigest.eval.rubric import rubric
from aidigest.generate._shared import (
    parse_json_obj,
    sources_block,
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

_CANDIDATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "lede": {"type": "string"},
        "body_markdown": {"type": "string"},
        "shortlist": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "one_liner": {"type": "string"},
                    "family": {
                        "type": "string",
                        "enum": [f.value for f in Family],
                    },
                },
            },
        },
        "on_my_radar": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "one_liner": {"type": "string"},
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
    """Generate one editorial candidate (raw JSON string)."""
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
    return await llm.generate(messages, json_schema=_CANDIDATE_SCHEMA, temperature=temperature)


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
    return await llm.generate(messages, json_schema=_CANDIDATE_SCHEMA, temperature=0.3)


def _parse_entries(raw_list: object) -> list[WeeklyShortlistEntry]:
    """Build WeeklyShortlistEntry list from parsed JSON, skipping bad rows."""
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
        url = row.get("url")
        entries.append(
            WeeklyShortlistEntry(
                title=title,
                url=str(url).strip() if url else None,
                one_liner=str(row.get("one_liner") or "").strip(),
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

    # 1. Best-of-N candidate drafts (concurrent).
    candidates = list(
        await asyncio.gather(
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
            )
        )
    )

    # 2. Judge -> winner (independent judge client).
    context = f"Weekly digest for {week_of}. Overall tier: {overall_tier.value}. Quiet week: {quiet_week}."
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
    parsed = parse_json_obj(polished_raw)
    if not parsed:  # polish failed to parse -> fall back to the winning draft
        parsed = parse_json_obj(candidates[winner_idx])

    title = str(parsed.get("title") or "").strip() or f"Week at a Glance — {week_of}"
    lede = str(parsed.get("lede") or "").strip()
    body = str(parsed.get("body_markdown") or "").strip()
    if quiet_week and not body:
        body = "Quiet week — nothing major shipped."

    return WeeklyDigest(
        id=_iso_week_id(week_of),
        kind=DigestKind.WEEKLY,
        week_of=week_of,
        title=title,
        lede=lede,
        body_markdown=body,
        overall_tier=overall_tier,
        quiet_week=quiet_week,
        shortlist=_parse_entries(parsed.get("shortlist")),
        on_my_radar=_parse_entries(parsed.get("on_my_radar")),
        story_ids=[s.id for s in tagged],
        candidate_count=n,
        winning_candidate=winner_idx,
        model=getattr(client, "model", ""),
        judge_model=getattr(judge_client, "model", ""),
        eval_scores=eval_scores,
    )


def _winner_scores(verdict: dict, winner_idx: int) -> dict:
    """Extract the winning candidate's per-criterion scores from a judge verdict."""
    scores = verdict.get("scores")
    if isinstance(scores, list) and 0 <= winner_idx < len(scores):
        row = scores[winner_idx]
        if isinstance(row, dict):
            return dict(row)
    return {}


__all__ = ["generate_weekly"]
