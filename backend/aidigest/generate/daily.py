"""Daily digest generation: map-reduce over the day's top stories.

Map step (``map_story``): each top story -> a :class:`StorySummary` whose depth
honors the story's :class:`ImportanceTier` (BREAKTHROUGH = full depth + context;
NOTABLE = 2-4 sentences; MINOR = one line; QUIET_DAY = honest "nothing major").

Reduce step (``generate_daily``): summaries are grouped into family-keyed
:class:`DigestSection`s (Academia / Industry / Community / Meta) and a one-line
TL;DR is written. Quiet days are handled honestly — the TL;DR says so and the
sections stay short. No manufactured importance.

The flexibility principle is enforced in two places: the tier is computed by
``generate.importance.classify_day`` and is also injected into the prompt so the
model honors it (see ``generate/prompts/daily_map.md``).
"""

from __future__ import annotations

import asyncio
import re

from aidigest.generate._shared import (
    parse_json_obj,
    sources_block,
    story_links,
    subfields_str,
)
from aidigest.generate.importance import classify_day, classify_day_tier
from aidigest.generate.prompts import (
    DAILY_MAP,
    load_prompt,
    tier_instruction,
    voice_prompt,
)
from aidigest.llm.base import LLMClient, Message
from aidigest.llm.factory import get_llm
from aidigest.models import (
    DailyDigest,
    DigestKind,
    DigestSection,
    Family,
    ImportanceTier,
    Item,
    Story,
    StorySummary,
)
from aidigest.process._vec import cosine

# JSON schema for the per-story map output.
_MAP_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "takeaway": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

_REDUCE_SCHEMA: dict = {
    "type": "object",
    "properties": {"tldr": {"type": "string"}},
}

# Ordered families for stable section ordering (academia first — the differentiator).
_FAMILY_ORDER: list[Family] = [
    Family.ACADEMIA,
    Family.INDUSTRY,
    Family.COMMUNITY,
    Family.META,
]

_FAMILY_HEADINGS: dict[Family, str] = {
    Family.ACADEMIA: "Academia",
    Family.INDUSTRY: "Industry",
    Family.COMMUNITY: "Community",
    Family.META: "Meta",
}

_QUIET_TLDR = "Quiet day — nothing major shipped."
_QUIET_TLDR_BRIEF = "Quiet day — nothing major shipped, but a few things worth a glance."


# Defensive length caps — a reasoning model can occasionally degenerate into a
# multi-thousand-char run-on sentence; never let that reach the digest.
_WHY_LIMIT = 400
_TAKEAWAY_LIMITS: dict[ImportanceTier, int] = {
    ImportanceTier.MINOR: 320,
    ImportanceTier.NOTABLE: 700,
    ImportanceTier.BREAKTHROUGH: 1600,
}


def _clip(text: str, limit: int) -> str:
    """Trim to <= limit chars at a sentence/word boundary (adds … when cut)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a clean SENTENCE end; only fall back to a clause boundary if a whole
    # sentence fits. A sentence end keeps its period; a clause cut drops the dangling
    # separator and trails an ellipsis (never "…optimize smaller,").
    sentence_idx = cut.rfind(". ")
    if sentence_idx > limit * 0.5:
        return cut[: sentence_idx + 1].strip()
    for sep in ("; ", ", "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[:idx].strip() + "…"
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip() + "…"


def _canonical_tags(tags: list[str], profile: dict) -> list[str]:
    """Map model tags to the EXACT subfield strings from the profile (consistent
    casing/punctuation): "Efficient & Scalable NLP" and "Efficient and Scalable
    NLP" both normalize to the one canonical form. Unknown tags pass through."""

    def _norm(s: str) -> str:
        return s.lower().replace("&", "and").replace("  ", " ").strip()

    canonical = {_norm(s): s for s in (profile.get("subfields") or [])}
    return [canonical.get(_norm(t), t) for t in tags]


async def map_story(
    story: Story,
    items_by_id: dict[str, Item],
    *,
    profile: dict,
    llm: LLMClient,
) -> StorySummary:
    """Map one story to a :class:`StorySummary`, honoring its tier.

    QUIET_DAY stories are not sent to the LLM — they get a short honest line so we
    never pay tokens to manufacture importance. All other tiers are written by the
    model with a tier-scaled instruction.
    """
    tier = story.tier
    links = _trim_links(story_links(story, items_by_id), 3)

    if tier == ImportanceTier.QUIET_DAY:
        return StorySummary(
            story_id=story.id,
            title=story.title,
            family=story.family,
            tier=tier,
            takeaway="Minor item; nothing major here.",
            why_it_matters="",
            links=links,
            tags=[],
            score=story.final_rank,
        )

    prompt_body = load_prompt(DAILY_MAP).format(
        title=story.title,
        family=story.family.value,
        tier=tier.value,
        mention_count=story.mention_count,
        subfields=subfields_str(profile),
        sources=sources_block(story, items_by_id),
        tier_instruction=tier_instruction(tier),
    )
    messages = [
        Message(role="system", content=voice_prompt()),
        Message(role="user", content=prompt_body),
    ]
    raw = await llm.generate(messages, json_schema=_MAP_SCHEMA, temperature=0.6)
    parsed = parse_json_obj(raw)

    takeaway = _clip(
        str(parsed.get("takeaway") or "").strip() or story.title,
        _TAKEAWAY_LIMITS.get(tier, 700),
    )
    why = _clip(str(parsed.get("why_it_matters") or "").strip(), _WHY_LIMIT)
    tags_raw = parsed.get("tags") or []
    tags = _canonical_tags(
        [str(t).strip() for t in tags_raw if str(t).strip()][:3], profile
    )

    return StorySummary(
        story_id=story.id,
        title=story.title,
        family=story.family,
        tier=tier,
        takeaway=takeaway,
        why_it_matters=why,
        links=links,
        tags=tags,
        score=story.final_rank,
    )


# --------------------------------------------------------------------------- #
# Trend recaps (smol.ai-style) — a per-family paragraph + brief links
# --------------------------------------------------------------------------- #

_TREND_SCHEMA: dict = {"type": "object", "properties": {"summary": {"type": "string"}}}

# How many brief trend link-items to list under each family section.
_BRIEF_CAP_PER_FAMILY = 6

_TREND_FOCUS: dict[Family, str] = {
    Family.ACADEMIA: (
        "the day's RESEARCH themes on arXiv/HF — the methods and results, and which "
        "direction the field is pushing — for someone tracking {subfields}"
    ),
    Family.COMMUNITY: (
        "what the AI community is actually DISCUSSING today (the threads, tools, and "
        "debates getting real traction)"
    ),
    Family.INDUSTRY: "what the AI labs and companies SHIPPED or announced today",
    Family.META: "what the AI curators/newsletters are HIGHLIGHTING today",
}


def _is_lead(story: Story) -> bool:
    """Worth an individual full takeaway. Any breakthrough qualifies; a notable item
    qualifies UNLESS it is academia — academia is summarized as research trends +
    links (the reader's explicit ask) unless it is a genuine breakthrough."""
    if story.tier == ImportanceTier.BREAKTHROUGH:
        return True
    return story.tier == ImportanceTier.NOTABLE and story.family != Family.ACADEMIA


_ANNOUNCE_RE = re.compile(
    r"\b(unveils?|introduc\w+|releas\w+|launch\w+|announc\w+|ships?|debuts?|"
    r"available|open[- ]?sourc\w+|presents?)\b",
    re.IGNORECASE,
)
_ESSAY_RE = re.compile(
    r"^\s*(how |why |what |when |the case for|thoughts on|reflections|is |are |should |has )",
    re.IGNORECASE,
)


def _announce_score(title: str) -> float:
    """Heuristic newsworthiness of a title: a concrete release/announcement (1.0)
    should lead over an opinion/essay (0.0); everything else is neutral (0.4).
    Makes the section LEAD the actual news, not a marketing/blog essay."""
    t = title or ""
    if _ANNOUNCE_RE.search(t):
        return 1.0
    if _ESSAY_RE.match(t):
        return 0.0
    return 0.4


def _lead_sort_key(story: Story) -> tuple[float, float]:
    """Order leads so the most newsworthy is first: breakthroughs, then concrete
    announcements, then by objective importance (not personal-inflated rank)."""
    bt = 1.0 if story.tier == ImportanceTier.BREAKTHROUGH else 0.0
    return (bt + _announce_score(story.title), float(story.importance))


# A cross-source story can embed just below the cluster bar (0.86) and survive as two
# stories. Collapse such near-duplicate LEADS so the same news is not shown twice —
# but only well above embedding noise (~0.71), and never two distinct breakthroughs.
_LEAD_DEDUP_COSINE = 0.84


def _is_dup_lead(cand: Story, kept: Story) -> bool:
    """True if `cand` is the same real-world story as an already-kept lead.

    Two breakthroughs are never collapsed — distinct majors each deserve a lead, and a
    genuinely-same breakthrough would have merged at the cluster bar already. Otherwise
    a high centroid cosine (just below the cluster threshold) means the same news
    arrived from two sources and embedded a hair under the merge bar."""
    if cand.tier == ImportanceTier.BREAKTHROUGH and kept.tier == ImportanceTier.BREAKTHROUGH:
        return False
    if not (cand.embedding and kept.embedding):
        return False
    return cosine(cand.embedding, kept.embedding) >= _LEAD_DEDUP_COSINE


def _dedupe_leads(leads: list[Story]) -> tuple[list[Story], set[str]]:
    """Collapse near-duplicate leads, keeping the higher-ranked one (input is
    pre-sorted) and folding the duplicate's item_ids in so its source LINK is
    preserved — never dropped. Returns (kept leads, set of absorbed story ids) so the
    caller can also keep the absorbed stories out of the per-family trend recaps."""
    kept: list[Story] = []
    absorbed: set[str] = set()
    for cand in leads:
        dup_idx = next((i for i, k in enumerate(kept) if _is_dup_lead(cand, k)), -1)
        if dup_idx == -1:
            kept.append(cand)
            continue
        k = kept[dup_idx]
        merged_ids = k.item_ids + [i for i in cand.item_ids if i not in k.item_ids]
        kept[dup_idx] = k.model_copy(
            update={"item_ids": merged_ids, "mention_count": len(merged_ids)}
        )
        absorbed.add(cand.id)
    return kept, absorbed


def _trim_links(links: list[str], n: int) -> list[str]:
    """Dedupe (preserving order) and cap — no more 5-link soup per item."""
    seen: set[str] = set()
    out: list[str] = []
    for u in links:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= n:
            break
    return out


def _brief_summary(story: Story, items_by_id: dict[str, Item]) -> StorySummary:
    """A link-only item (empty takeaway) listed under a section's trend recap."""
    return StorySummary(
        story_id=story.id,
        title=story.title,
        family=story.family,
        tier=story.tier,
        takeaway="",
        why_it_matters="",
        links=_trim_links(story_links(story, items_by_id), 2),
        tags=[],
        score=story.final_rank,
    )


# The day's top stories lead the digest as a cross-family "Top Stories" section.
_TOP_STORIES_CAP = 6
_TOP_HEADING = "⚡ Top Stories"


def _source_snippet(story: Story, items_by_id: dict[str, Item], *, limit: int = 240) -> str:
    """A short cleaned slice of the representative item's text (for number-grounding)."""
    item = items_by_id.get(story.representative_item_id or "")
    text = (item.raw_text if item else "") or ""
    return " ".join(text.split())[:limit]


async def _trend_intro(
    family: Family,
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    profile: dict,
    llm: LLMClient,
) -> str:
    """A short (2-3 sentence) trend recap over a family's stories. '' if none."""
    if not stories:
        return ""
    rows: list[str] = []
    for i, s in enumerate(stories[:14]):
        snip = _source_snippet(s, items_by_id)
        rows.append(f"{i + 1}. {s.title}" + (f" — {snip}" if snip else ""))
    listing = "\n".join(rows)
    focus = _TREND_FOCUS.get(family, "what's happening").format(
        subfields=subfields_str(profile)
    )
    prompt = (
        f"Write a SHORT trend recap (2-3 dense sentences) of {focus}, based ONLY on "
        "the items below (title — source snippet).\n"
        "- SYNTHESIZE: name the through-line or tension connecting them — do NOT just "
        "enumerate. Lead with the single most important/striking finding.\n"
        "- CITE A CONCRETE NUMBER or result when the snippets contain one (a benchmark "
        "score, speedup, %, token/param count, $ figure). This is what separates "
        "researcher-grade synthesis from an executive summary.\n"
        "- Plain, short, assertive sentences. BANNED phrases: 'various advances', "
        "'curators are focusing on', 'this digest', 'critical failure modes', and any "
        "marketing fluff or narration about what was selected.\n"
        "- If little is genuinely happening, say so plainly in one sentence.\n"
        'Return JSON {"summary": "..."}.\n\n'
        f"{listing}\n"
    )
    raw = await llm.generate(
        [Message(role="system", content=voice_prompt()), Message(role="user", content=prompt)],
        json_schema=_TREND_SCHEMA,
        temperature=0.4,
    )
    return _clip(str(parse_json_obj(raw).get("summary") or "").strip().rstrip(" ,;"), 500)


async def _top_stories_section(
    leads: list[Story], items_by_id: dict[str, Item], *, profile: dict, llm: LLMClient
) -> DigestSection | None:
    """The cross-family lead section: the day's biggest items, full depth, ordered by
    newsworthiness. This is what a breakthrough leads with — never buried in a family."""
    if not leads:
        return None
    summaries = list(
        await asyncio.gather(
            *(map_story(s, items_by_id, profile=profile, llm=llm) for s in leads)
        )
    )
    return DigestSection(family=leads[0].family, heading=_TOP_HEADING, summaries=summaries)


async def _build_trend_section(
    family: Family,
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    profile: dict,
    llm: LLMClient,
) -> DigestSection | None:
    """A trend-recap section (no full leads — those are in Top Stories): a synthesis
    intro paragraph + brief links for the family's remaining items."""
    if not stories:
        return None
    intro = await _trend_intro(family, stories, items_by_id, profile=profile, llm=llm)
    brief = [_brief_summary(s, items_by_id) for s in stories[:_BRIEF_CAP_PER_FAMILY]]
    if not intro and not brief:
        return None
    return DigestSection(
        family=family, heading=_FAMILY_HEADINGS[family], intro=intro, summaries=brief
    )


async def _daily_tldr(
    day_tier: ImportanceTier,
    grounding_titles: list[str],
    has_sections: bool,
    *,
    date: str,
    llm: LLMClient,
) -> str:
    """Headline TL;DR. Quiet days are honest; notable/breakthrough name the lead —
    grounded STRICTLY in the day's actual stories (never invents a number/topic)."""
    if day_tier == ImportanceTier.QUIET_DAY or not grounding_titles:
        return _QUIET_TLDR_BRIEF if has_sections else _QUIET_TLDR
    items = "\n".join(f"- {t}" for t in grounding_titles[:8])
    label = "a MAJOR day" if day_tier == ImportanceTier.BREAKTHROUGH else "a notable day"
    prompt = (
        f"Write ONE punchy TL;DR line (<= 22 words) for today's AI digest, {label}.\n"
        "Lead with the single biggest item BELOW. Use ONLY these stories — do NOT "
        "invent any number, benchmark, model, or topic that is not in this list.\n"
        "Plain, dense, no hype/marketing.\n"
        f"Stories:\n{items}\n"
        'Return JSON {"tldr": "..."}.'
    )
    raw = await llm.generate(
        [Message(role="system", content=voice_prompt()), Message(role="user", content=prompt)],
        json_schema=_REDUCE_SCHEMA,
        temperature=0.4,
    )
    tldr = _clip(str(parse_json_obj(raw).get("tldr") or "").strip().rstrip(",;"), 220)
    return tldr or "Today in AI."


async def generate_daily(
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    profile: dict,
    date: str,
    llm: LLMClient | None = None,
    max_items: int | None = None,  # noqa: ARG001 — kept for API compat (per-family caps now)
) -> DailyDigest:
    """Generate the daily digest in the smol.ai shape. ``id = f'daily-{date}'``.

    The day is classified into a 3-LEVEL tier (BREAKTHROUGH / NOTABLE / QUIET_DAY).
    Each family becomes a section with a trend-recap intro + (only the genuinely
    breakthrough/notable items as) full takeaways + brief links for the rest — so
    academia is a research-trends summary with arXiv links rather than a wall of
    per-paper entries, community is always present as a pulse, and full depth is
    reserved for real breakthroughs.
    """
    client = llm or get_llm()

    tagged, _overall, _legacy_quiet = classify_day(stories, profile=profile)
    day_tier = classify_day_tier(stories, profile=profile)
    quiet_day = day_tier == ImportanceTier.QUIET_DAY

    # The day's biggest items lead the digest cross-family (Top Stories); everything
    # else is a per-family trend recap over the NON-lead items. Collapse cross-source
    # near-duplicates BEFORE capping so the same news never takes two of six slots.
    ranked_leads = sorted((s for s in tagged if _is_lead(s)), key=_lead_sort_key, reverse=True)
    deduped_leads, absorbed_ids = _dedupe_leads(ranked_leads)
    leads = deduped_leads[:_TOP_STORIES_CAP]
    # A NOTABLE/BREAKTHROUGH day MUST lead with a Top Story — if the tiers left none
    # (e.g. importance crosses the day bar but no story reached lead tier), promote
    # the single most newsworthy item so the structure matches the day tier.
    if not leads and day_tier != ImportanceTier.QUIET_DAY:
        pool = sorted(
            tagged, key=lambda s: (float(s.importance), _announce_score(s.title)), reverse=True
        )
        if pool:
            leads = [pool[0].model_copy(update={"tier": ImportanceTier.NOTABLE})]
    # Exclude both the leads and any absorbed duplicates from the trend recaps so a
    # collapsed story is never shown again as a brief link.
    lead_ids = {s.id for s in leads} | absorbed_ids
    by_family = {
        fam: [s for s in tagged if s.family == fam and s.id not in lead_ids]
        for fam in _FAMILY_ORDER
    }

    results = await asyncio.gather(
        _top_stories_section(leads, items_by_id, profile=profile, llm=client),
        *(
            _build_trend_section(fam, by_family[fam], items_by_id, profile=profile, llm=client)
            for fam in _FAMILY_ORDER
            if by_family[fam]
        ),
    )
    top_section, trend_sections = results[0], [s for s in results[1:] if s is not None]
    sections = ([top_section] if top_section else []) + trend_sections

    # Ground the TL;DR strictly in real titles (lead titles + top trend titles) so it
    # can never fabricate a number/topic absent from the body.
    brief_titles = [su.title for sec in sections for su in sec.summaries if not su.takeaway]
    grounding = [s.title for s in leads] + brief_titles[:6]
    tldr = await _daily_tldr(day_tier, grounding, bool(sections), date=date, llm=client)
    story_ids = [su.story_id for sec in sections for su in sec.summaries]

    return DailyDigest(
        id=f"daily-{date}",
        kind=DigestKind.DAILY,
        date=date,
        tldr=tldr,
        overall_tier=day_tier,
        quiet_day=quiet_day,
        sections=sections,
        story_ids=story_ids,
        model=getattr(client, "model", ""),
    )


__all__ = ["generate_daily", "map_story"]
