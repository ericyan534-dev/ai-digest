"""Embed Items in batch via the active LLM client.

Embeddings are 1536-dim, L2-normalized unit vectors (the mock and the real
Gemini client both guarantee this). Items that already carry an embedding are
returned unchanged.
"""

from __future__ import annotations

from aidigest.config import get_settings
from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm
from aidigest.models import Item

# How much of the body to feed the embedder (title + lead). Embedding the whole
# article wastes tokens; the lead carries the topical signal we cluster on.
_LEAD_CHARS = 1200


def embedding_text(item: Item) -> str:
    """Canonical text fed to the embedder: title + the lead of the body."""
    lead = (item.raw_text or "")[:_LEAD_CHARS].strip()
    if lead:
        return f"{item.title}\n{lead}"
    return item.title


async def embed_items(items: list[Item], *, llm: LLMClient | None = None) -> list[Item]:
    """Return NEW items with `.embedding` set for those missing one.

    Items that already have an embedding are passed through untouched. Embedding
    length equals ``settings.embed_dim`` (1536).
    """
    if not items:
        return []
    client = llm or get_llm()

    pending = [it for it in items if it.embedding is None]
    if not pending:
        return list(items)

    vectors = await client.embed(
        [embedding_text(it) for it in pending], task_type="RETRIEVAL_DOCUMENT"
    )
    embed_dim = get_settings().embed_dim
    by_id: dict[str, list[float]] = {}
    for item, vec in zip(pending, vectors, strict=True):
        # Defensive: guarantee the contracted dimensionality downstream.
        if len(vec) != embed_dim:
            vec = list(vec[:embed_dim]) + [0.0] * max(0, embed_dim - len(vec))
        by_id[item.id] = vec

    return [it.with_embedding(by_id[it.id]) if it.id in by_id else it for it in items]


__all__ = ["embed_items", "embedding_text"]
