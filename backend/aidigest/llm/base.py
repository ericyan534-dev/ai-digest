"""LLM client contract (Protocol) + shared data types.

ALL LLM clients (mock and real Gemini) MUST satisfy `LLMClient`.

=== HARD REQUIREMENTS the real Gemini implementer MUST obey ===

1. REASONING MODEL (gemini-3.5-flash):
   A generateContent call spends "thoughts" tokens and the response `parts`
   may contain THOUGHT parts (part.thought == true) mixed with answer parts.
   - Set a GENEROUS maxOutputTokens (default 8192 via settings) so visible
     text is not starved by thinking.
   - Collect ONLY non-thought text parts (skip parts where `thought` is truthy).
   - Gracefully handle finishReason == "MAX_TOKENS": return whatever text was
     produced (never raise) and let callers detect truncation.

2. RETRY (network is intermittent — TLS resets even when up):
   EVERY outbound HTTP call MUST use exponential-backoff retry with >= 5 tries
   (tenacity). Use `aidigest.ingest.base.make_async_client()` /
   `with_retry(...)` so retry policy is shared and consistent.

3. EMBEDDINGS (gemini-embedding-001):
   Default output is 3072-dim, which EXCEEDS pgvector's 2000-dim index limit.
   ALWAYS request `outputDimensionality=1536` (Matryoshka) and L2-normalize the
   returned vectors before returning/storing. Returned vectors MUST have length
   == settings.embed_dim (1536). Use taskType "RETRIEVAL_DOCUMENT" for items and
   "RETRIEVAL_QUERY" for the interest/query vector.

4. SECRETS:
   Read GEMINI_API_KEY from settings (get_settings()). NEVER hardcode it.

5. JSON MODE:
   When `json_schema` is provided, instruct the model to return JSON conforming
   to it (responseMimeType "application/json" + responseSchema if supported) and
   parse the visible text into a dict. The mock mirrors this contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class Message(BaseModel):
    """A single chat message. `role` is one of: system | user | assistant."""

    role: str
    content: str


class GenerationResult(BaseModel):
    """Structured result of a generate() call (used when callers need metadata)."""

    text: str
    truncated: bool = False  # True when finishReason == MAX_TOKENS
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    model: str = ""


# A JSON schema dict (subset) or None.
JsonSchema = dict | None


@runtime_checkable
class LLMClient(Protocol):
    """The single LLM abstraction every stage depends on.

    Implementations: `MockLLMClient` (offline, deterministic) and
    `GeminiClient` (real REST). Select via `aidigest.llm.factory.get_llm()`.
    """

    model: str
    embed_model: str
    embed_dim: int

    async def generate(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int = 8192,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> str:
        """Generate text (or a JSON string when json_schema is provided).

        Args:
            prompt: a single prompt string OR a list of Message turns.
            max_output_tokens: generous budget (reasoning model spends thoughts).
            temperature: sampling temperature.
            json_schema: when provided, the model returns JSON conforming to it;
                the returned string is parseable JSON.

        Returns:
            The visible (non-thought) text. NEVER raises on MAX_TOKENS — returns
            whatever was produced. Raises only on unrecoverable transport errors
            after retries are exhausted.
        """
        ...

    async def generate_detailed(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int = 8192,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> GenerationResult:
        """Like `generate()` but returns a GenerationResult with token/truncation metadata."""
        ...

    async def embed(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Embed texts -> list of L2-normalized vectors, each of length embed_dim (1536).

        Args:
            texts: input strings (batched by the implementation as needed).
            task_type: "RETRIEVAL_DOCUMENT" (items) or "RETRIEVAL_QUERY" (interest vector).

        Returns:
            One unit vector per input text; len(vec) == embed_dim for every vector.
        """
        ...

    async def judge(
        self,
        *,
        candidates: list[str],
        rubric: dict,
        context: str = "",
    ) -> dict:
        """LLM-as-judge: score candidates against a rubric, pick a winner.

        Returns a dict shaped as:
            {
              "winner": int,                       # index into candidates
              "scores": [ {<criterion>: float, ...}, ... ],  # per-candidate
              "rationale": str,
            }
        Implementations SHOULD use a separate/independent judging pass.
        """
        ...
