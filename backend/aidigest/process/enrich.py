"""Enrich stories: cheap LLM titling + citation-velocity awareness.

This is a no-op-safe pass: it NEVER drops a story and never raises on a single
bad LLM response. When the LLM yields a cleaner headline we adopt it; otherwise
the original title stands. Citation velocity (and other item-level signals) are
summarized here so ranking/generation can reason about academia momentum.
"""

from __future__ import annotations

import json

from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm
from aidigest.models import Family, Item, Story
from aidigest.process._signals import citation_velocity

# Only re-title stories with multiple sources or academia provenance; titling a
# single community post wastes a call and rarely improves the headline.
_MIN_MENTIONS_FOR_RETITLE = 2

_TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}


async def enrich_stories(
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    llm: LLMClient | None = None,
) -> list[Story]:
    """Return NEW stories with improved titles where useful (no-op-safe)."""
    if not stories:
        return []
    client = llm or get_llm()

    enriched: list[Story] = []
    for story in stories:
        new_title = await _maybe_retitle(story, items_by_id, client)
        if new_title and new_title != story.title:
            enriched.append(story.model_copy(update={"title": new_title}))
        else:
            enriched.append(story)
    return enriched


def story_citation_velocity(story: Story, items_by_id: dict[str, Item]) -> float:
    """Max citation velocity across a story's member items (academia momentum)."""
    members = [items_by_id[i] for i in story.item_ids if i in items_by_id]
    if not members:
        return 0.0
    return max((citation_velocity(it) for it in members), default=0.0)


async def _maybe_retitle(
    story: Story, items_by_id: dict[str, Item], client: LLMClient
) -> str | None:
    if story.mention_count < _MIN_MENTIONS_FOR_RETITLE and story.family != Family.ACADEMIA:
        return None
    members = [items_by_id[i] for i in story.item_ids if i in items_by_id]
    if not members:
        return None
    titles = "\n".join(f"- {m.title}" for m in members[:6])
    prompt = (
        "Write ONE short, plain, technical headline (<= 12 words, no marketing "
        "adjectives) covering these related items. Return JSON {\"title\": ...}.\n"
        f"{titles}"
    )
    try:
        out = await client.generate(prompt, temperature=0.0, json_schema=_TITLE_SCHEMA)
        parsed = json.loads(out)
        title = parsed.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None
    return None


__all__ = ["enrich_stories", "story_citation_velocity"]
