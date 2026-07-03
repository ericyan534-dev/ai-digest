"""Story enrichment — signal summaries only. Titles are NEVER rewritten.

A story's title is ALWAYS the real title of its representative source item. An
earlier version asked an LLM to "write a headline covering these related items",
which hallucinated fabricated titles (e.g. the smol.ai roundup "not much happened
today" became "AI News Summary Reports Minimal Industry Activity"). That is
dishonest and confusing, so generative re-titling was removed: what the reader
sees is what a real source published.

``enrich_stories`` is kept as a no-op-safe pass (it never drops or mutates a
story) so the pipeline keeps a stable extension point for future, GROUNDED
enrichment. ``story_citation_velocity`` summarizes academia momentum for callers.
"""

from __future__ import annotations

from aidigest.llm.base import LLMClient
from aidigest.models import Item, Story
from aidigest.process._signals import citation_velocity


async def enrich_stories(
    stories: list[Story],
    items_by_id: dict[str, Item],  # noqa: ARG001 — reserved for grounded enrichment
    *,
    llm: LLMClient | None = None,  # noqa: ARG001 — kept for API/pipeline compatibility
) -> list[Story]:
    """Return the stories unchanged. Titles stay verbatim from the source item."""
    return list(stories)


def story_citation_velocity(story: Story, items_by_id: dict[str, Item]) -> float:
    """Max citation velocity across a story's member items (academia momentum)."""
    members = [items_by_id[i] for i in story.item_ids if i in items_by_id]
    if not members:
        return 0.0
    return max((citation_velocity(it) for it in members), default=0.0)


__all__ = ["enrich_stories", "story_citation_velocity"]
