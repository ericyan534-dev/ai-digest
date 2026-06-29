"""Deterministic, offline MockLLMClient (no network, no API key).

Used when AIDIGEST_LLM_MOCK=1 and by ALL tests. Output is stable and
hash-seeded from the input so re-runs are identical (idempotency principle).

Contract guarantees mirrored from `base.LLMClient`:
  * embed() returns L2-normalized vectors of length `embed_dim` (default 1536).
  * generate(json_schema=...) returns a parseable JSON string.
  * generate() returns plausible structured text derived from the prompt.
  * judge() deterministically picks a winner and returns per-candidate scores.
"""

from __future__ import annotations

import hashlib
import json
import math
import random

from aidigest.llm.base import GenerationResult, JsonSchema, Message


def _seed_from(text: str) -> int:
    """Stable integer seed derived from text via sha256."""
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _prompt_to_text(prompt: str | list[Message]) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(f"{m.role}: {m.content}" for m in prompt)


class MockLLMClient:
    """Deterministic offline LLM. Satisfies the `LLMClient` Protocol structurally."""

    def __init__(
        self,
        *,
        model: str = "mock-flash",
        embed_model: str = "mock-embed",
        embed_dim: int = 1536,
    ) -> None:
        self.model = model
        self.embed_model = embed_model
        self.embed_dim = embed_dim

    # ----------------------------------------------------------------- generate
    async def generate(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int = 8192,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> str:
        result = await self.generate_detailed(
            prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )
        return result.text

    async def generate_detailed(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int = 8192,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> GenerationResult:
        text = _prompt_to_text(prompt)
        seed = _seed_from(text)
        if json_schema is not None:
            payload = _mock_json_for_schema(json_schema, seed)
            out = json.dumps(payload, ensure_ascii=False)
        else:
            out = _mock_prose(text, seed)
        return GenerationResult(
            text=out,
            truncated=False,
            prompt_tokens=max(1, len(text) // 4),
            output_tokens=max(1, len(out) // 4),
            thought_tokens=8,
            model=self.model,
        )

    # ------------------------------------------------------------------- embed
    async def embed(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        return [self._unit_vector(t) for t in texts]

    def _unit_vector(self, text: str) -> list[float]:
        """Deterministic L2-normalized vector of length embed_dim."""
        rng = random.Random(_seed_from(text))
        vec = [rng.gauss(0.0, 1.0) for _ in range(self.embed_dim)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    # ------------------------------------------------------------------- judge
    async def judge(
        self,
        *,
        candidates: list[str],
        rubric: dict,
        context: str = "",
    ) -> dict:
        criteria = list(rubric.get("criteria", {}).keys()) or [
            "insight",
            "accuracy",
            "narrative",
            "personal_fit",
            "honesty",
        ]
        scores: list[dict[str, float]] = []
        totals: list[float] = []
        for cand in candidates:
            seed = _seed_from(context + "\n" + cand)
            rng = random.Random(seed)
            per = {c: round(1.0 + rng.random() * 4.0, 2) for c in criteria}  # 1..5
            scores.append(per)
            totals.append(sum(per.values()))
        winner = max(range(len(candidates)), key=lambda i: totals[i]) if candidates else 0
        return {
            "winner": winner,
            "scores": scores,
            "rationale": "mock-judge: selected highest aggregate rubric score",
        }


# --------------------------------------------------------------------------- #
# Deterministic content generators
# --------------------------------------------------------------------------- #


def _mock_prose(text: str, seed: int) -> str:
    rng = random.Random(seed)
    leads = [
        "Quiet day — nothing major shipped.",
        "Steady cadence today; a couple of papers worth a skim.",
        "Big one: a release that resets expectations.",
    ]
    lead = leads[seed % len(leads)]
    snippet = " ".join(text.split()[:12])
    return (
        f"{lead} Context: {snippet}. "
        f"Signal over fluff. Short sentences. {rng.choice(['', 'Worth noting.'])}"
    ).strip()


def _mock_json_for_schema(schema: JsonSchema, seed: int) -> object:
    """Build a deterministic value matching a (subset of) JSON Schema."""
    if schema is None:
        return {}
    stype = schema.get("type")
    if stype == "object":
        props = schema.get("properties", {})
        return {k: _mock_json_for_schema(v, seed + i) for i, (k, v) in enumerate(props.items())}
    if stype == "array":
        item_schema = schema.get("items", {"type": "string"})
        n = int(schema.get("minItems", 1)) or 1
        return [_mock_json_for_schema(item_schema, seed + j) for j in range(n)]
    if stype == "number":
        return round((seed % 1000) / 1000.0, 3)
    if stype == "integer":
        return seed % 100
    if stype == "boolean":
        return bool(seed % 2)
    # default: string
    enum = schema.get("enum")
    if enum:
        return enum[seed % len(enum)]
    return f"mock-{seed % 10000}"
