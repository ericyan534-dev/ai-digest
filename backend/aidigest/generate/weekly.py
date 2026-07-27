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
    WEEKLY_METADATA,
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

# DELIBERATELY NOT RAISED. Measured against the real API on the production-shaped
# weekly prompt: a HEALTHY response needs ~960 output + ~2650 thought tokens, so
# the configured 8192 default already carries >2x headroom. The failures are not
# long answers being cut off — they are runaway generations that expand to fill
# whatever budget they are given (6.8k output tokens at 8192; 13.1k at 16384;
# at 32768 the request outran http_timeout_seconds and never returned at all).
# Raising the budget therefore buys nothing for a good week and lets a bad one
# burn more time and money before failing. The guards below are what make this
# survivable; see the module docstring.

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

# The weekly packs 20 stories into ONE request, so its per-story source budget is
# much tighter than the daily's (5 x 600 chars). See _story_blocks.
_WEEKLY_SOURCE_ITEMS = 2
_WEEKLY_SOURCE_CHARS = 280

# Metadata only — every field here is SHORT. The editorial body is generated as
# plain markdown in a separate call and never passes through JSON, because a long
# markdown document inside a JSON string is what made this unreliable: one
# degenerate run lost the whole object instead of one field.
_ENTRY_ARRAY: dict = {
    "type": "array",
    "maxItems": 8,
    "items": {
        "type": "object",
        "required": ["title", "one_liner", "family"],
        "properties": {
            "title": {"type": "string", "maxLength": 300},
            "url": {"type": "string", "maxLength": 500},
            "one_liner": {"type": "string", "maxLength": 400},
            "family": {"type": "string", "enum": [f.value for f in Family]},
        },
    },
}

_METADATA_SCHEMA: dict = {
    "type": "object",
    "required": ["title", "lede", "shortlist", "on_my_radar"],
    "propertyOrdering": ["title", "lede", "shortlist", "on_my_radar"],
    "properties": {
        "title": {
            "type": "string",
            "maxLength": 200,
            "description": "The editorial headline, under 15 words. Plain prose.",
        },
        "lede": {
            "type": "string",
            "maxLength": 600,
            "description": "The opening one or two sentences, copied from the editorial.",
        },
        "shortlist": _ENTRY_ARRAY,
        "on_my_radar": _ENTRY_ARRAY,
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
    """Render ranked stories (with tier tags) into a prompt block.

    Deliberately leaner than the daily's: the weekly puts ALL of these in one
    request, and at the daily's bounds that reached ~60k characters. Prompt size
    is what tracks the degenerate-generation failures — a dense ~15k-char block
    set failed 4 of 4 against the live API, a ~8k-char one succeeded 2 of 4. The
    weekly is synthesis, so it needs enough to recognise each story, not the full
    source text.
    """
    blocks: list[str] = []
    for story in stories[:limit]:
        blocks.append(
            f"### [{story.tier.value}] {story.title} "
            f"(family={story.family.value}, mentions={story.mention_count})\n"
            f"{sources_block(story, items_by_id, max_items=_WEEKLY_SOURCE_ITEMS, max_chars=_WEEKLY_SOURCE_CHARS)}"
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
    """Generate one editorial candidate as PLAIN MARKDOWN.

    Not JSON. A long markdown document inside a JSON string field is what made
    this call unreliable: every newline needs escaping, constrained decoding has
    to hold the string open for thousands of tokens, and a single degenerate run
    loses the ENTIRE object rather than one field. Plain text has no parse step
    to fail, so a partial answer is still a usable answer.
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
    result = await llm.generate_detailed(messages, temperature=temperature)
    if result.truncated:
        logger.warning(
            "weekly candidate %d/%d truncated (runaway generation); output_tokens=%d",
            index + 1,
            n_candidates,
            result.output_tokens,
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
    """Polish the winning draft. Markdown in, markdown out."""
    prompt_body = load_prompt(WEEKLY_POLISH).format(
        n_candidates=n_candidates,
        judge_rationale=rationale or "(no rationale provided)",
        winning_draft=winning_raw,
    )
    messages = [
        Message(role="system", content=voice_prompt()),
        Message(role="user", content=prompt_body),
    ]
    result = await llm.generate_detailed(messages, temperature=0.3)
    if result.truncated:
        logger.warning(
            "weekly polish truncated (runaway generation); output_tokens=%d",
            result.output_tokens,
        )
    return result.text


async def _metadata(
    *, body: str, week_of: str, story_lines: str, llm: LLMClient
) -> dict:
    """Derive title/lede/shortlist/radar from a finished editorial. Short fields only.

    Separated from the body on purpose: this call can fail without costing us the
    editorial, and every field it returns is short enough that a runaway has no
    room to develop.
    """
    prompt_body = load_prompt(WEEKLY_METADATA).format(
        week_of=week_of, editorial=body, story_lines=story_lines
    )
    messages = [
        Message(role="system", content=voice_prompt()),
        Message(role="user", content=prompt_body),
    ]
    try:
        raw = await llm.generate(
            messages, json_schema=_METADATA_SCHEMA, temperature=0.2
        )
    except Exception as exc:  # metadata is optional; the body already stands
        logger.warning("weekly metadata call failed: %s", type(exc).__name__)
        return {}
    parsed = parse_json_obj(raw)
    if not parsed:
        logger.warning("weekly metadata unparseable; deriving from the editorial")
    return parsed


def _title_from_body(body: str) -> str:
    """First markdown heading, else the first short line."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) <= 200:
            return stripped
    return ""


def _lede_from_body(body: str) -> str:
    """First non-heading paragraph, clipped to a sentence or two."""
    for block in body.split("\n\n"):
        text = " ".join(
            ln.strip() for ln in block.splitlines() if not ln.strip().startswith("#")
        ).strip()
        if len(text) < 20:
            continue
        if len(text) <= 400:
            return text
        cut = text[:400]
        stop = cut.rfind(". ")
        return (cut[: stop + 1] if stop > 100 else cut).strip()
    return ""


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

    # Prefer the polished draft, then the judge's winner, then ANY intact draft.
    # These are plain markdown now, so "usable" just means non-empty — there is no
    # parse step left to fail.
    body = _first_nonempty([polished_raw, *candidates[winner_idx : winner_idx + 1], *candidates])
    if not body:
        # NEVER ship an empty editorial. A quiet week says so honestly; otherwise
        # the LLM failed us and the reader still gets the week's real material
        # rather than a header over blank space (observed 2026-07-26).
        logger.error("weekly: no draft produced any text; using the story list")
        body = (
            "Quiet week — nothing major shipped."
            if quiet_week
            else _fallback_body(tagged, items_by_id)
        )

    # Metadata is a SEPARATE, short call. If it fails we still have the editorial,
    # and title/lede come straight out of the prose the model already wrote.
    meta = await _metadata(
        body=body,
        week_of=week_of,
        story_lines=_story_lines(tagged, items_by_id),
        llm=client,
    )
    title = (
        str(meta.get("title") or "").strip()
        or _title_from_body(body)
        or f"Week at a Glance — {week_of}"
    )
    lede = str(meta.get("lede") or "").strip() or _lede_from_body(body)

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
        shortlist=_parse_entries(meta.get("shortlist"), valid_urls),
        on_my_radar=_parse_entries(meta.get("on_my_radar"), valid_urls),
        story_ids=[s.id for s in tagged],
        candidate_count=n,
        winning_candidate=winner_idx,
        model=getattr(client, "model", ""),
        judge_model=getattr(judge_client, "model", ""),
        eval_scores=eval_scores,
    )


def _first_nonempty(drafts: list[str]) -> str:
    """First draft with actual text in it, stripped. '' when all are empty."""
    for index, raw in enumerate(drafts):
        text = _strip_fences(raw).strip()
        if text:
            if index > 0:
                logger.warning("weekly: draft %d was empty; used a later draft", index)
            return text
    return ""


def _strip_fences(raw: str) -> str:
    """Drop a wrapping ```markdown fence if the model added one."""
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if "\n" in body:
        body = body.split("\n", 1)[1]
    return body.removesuffix("```").strip()


def _story_lines(stories: list[Story], items_by_id: dict[str, Item], *, limit: int = 20) -> str:
    """One line per story — title, family, and its real URL — for the metadata call.

    Deliberately links-only: the metadata model must pick shortlist URLs from
    sources we actually hold, never invent them.
    """
    lines: list[str] = []
    for story in stories[:limit]:
        url = next((it.url for it in story_items(story, items_by_id) if it.url), "")
        lines.append(f"- {story.title} (family={story.family.value}) {url}".rstrip())
    return "\n".join(lines) if lines else "(no stories this week)"


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
