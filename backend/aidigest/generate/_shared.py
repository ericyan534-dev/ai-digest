"""Shared helpers for the generation stage (prompt formatting, JSON parsing).

Kept tiny and dependency-free so daily/weekly stay focused. All functions are
pure except where noted.
"""

from __future__ import annotations

import json

from aidigest.models import Item, Story

# How much per-item source text to feed the model (keep prompts dense, bounded).
_SOURCE_CHARS = 600
_MAX_SOURCE_ITEMS = 5


def parse_json_obj(raw: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences / stray text.

    Returns an empty dict when nothing parseable is found (callers supply
    fallbacks). Never raises.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def story_items(story: Story, items_by_id: dict[str, Item]) -> list[Item]:
    """Resolve a story's member items, representative first, missing ids skipped."""
    items: list[Item] = []
    rep_id = story.representative_item_id
    if rep_id and rep_id in items_by_id:
        items.append(items_by_id[rep_id])
    for iid in story.item_ids:
        if iid == rep_id:
            continue
        item = items_by_id.get(iid)
        if item is not None:
            items.append(item)
    return items


def sources_block(story: Story, items_by_id: dict[str, Item]) -> str:
    """Render a story's source items into a compact, bounded text block."""
    items = story_items(story, items_by_id)[:_MAX_SOURCE_ITEMS]
    if not items:
        return f"(no source text available; title: {story.title})"
    lines: list[str] = []
    for item in items:
        body = (item.raw_text or "").strip().replace("\n", " ")
        if len(body) > _SOURCE_CHARS:
            body = body[:_SOURCE_CHARS].rstrip() + "…"
        url = item.url or ""
        head = f"- [{item.source}] {item.title}".rstrip()
        if url:
            head += f" ({url})"
        lines.append(head)
        if body:
            lines.append(f"  {body}")
    return "\n".join(lines)


def story_links(story: Story, items_by_id: dict[str, Item]) -> list[str]:
    """Collect the de-duplicated source URLs for a story (order-preserving)."""
    seen: set[str] = set()
    links: list[str] = []
    for item in story_items(story, items_by_id):
        if item.url and item.url not in seen:
            seen.add(item.url)
            links.append(item.url)
    return links


def subfields_str(profile: dict) -> str:
    subfields = profile.get("subfields") or []
    return "; ".join(str(s) for s in subfields)


def venues_str(profile: dict) -> str:
    venues = profile.get("venues") or []
    return ", ".join(str(v) for v in venues)


__all__ = [
    "parse_json_obj",
    "story_items",
    "sources_block",
    "story_links",
    "subfields_str",
    "venues_str",
]
