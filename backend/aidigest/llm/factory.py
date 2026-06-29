"""LLM client factory: pick Mock or real Gemini based on AIDIGEST_LLM_MOCK.

Usage:
    from aidigest.llm.factory import get_llm
    llm = get_llm()           # honors settings (mock vs real)
    await llm.embed([...])

The returned object satisfies `aidigest.llm.base.LLMClient`.
"""

from __future__ import annotations

from aidigest.config import Settings, get_settings
from aidigest.llm.base import LLMClient
from aidigest.llm.mock import MockLLMClient


def get_llm(settings: Settings | None = None) -> LLMClient:
    """Return the active LLM client.

    * AIDIGEST_LLM_MOCK truthy  -> MockLLMClient (offline, deterministic).
    * otherwise                 -> GeminiClient (real REST; lazily imported so
                                   the mock path never needs the real deps/key).
    """
    settings = settings or get_settings()
    if settings.llm_mock:
        return MockLLMClient(
            model=settings.gemini_model,
            embed_model=settings.gemini_embed_model,
            embed_dim=settings.embed_dim,
        )
    # Lazy import keeps the offline/mock path free of network deps.
    from aidigest.llm.gemini import GeminiClient

    return GeminiClient(settings=settings)


def get_judge_llm(settings: Settings | None = None) -> LLMClient:
    """Return an INDEPENDENT judge client for the weekly best-of-N selection.

    Independence reduces self-bias (design §7.2/§7.3). When ``JUDGE_MODEL`` is set
    it points the judge at a different model; otherwise it is a distinct client
    instance of the same model (in mock mode it carries a distinct ``model`` name
    so ``judge_model`` is visibly separate from the generator's).
    """
    settings = settings or get_settings()
    if settings.llm_mock:
        return MockLLMClient(
            model="mock-judge",
            embed_model=settings.gemini_embed_model,
            embed_dim=settings.embed_dim,
        )
    from aidigest.llm.gemini import GeminiClient

    client = GeminiClient(settings=settings)
    if settings.weekly_judge_model:
        client.model = settings.weekly_judge_model
    return client
