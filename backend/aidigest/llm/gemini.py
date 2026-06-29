"""Real Google Gemini client (REST) implementing the `LLMClient` Protocol.

HARD REQUIREMENTS (see `aidigest/llm/base.py` docstring) honored here:

* gemini-3.5-flash is a REASONING model. A `generateContent` call spends
  "thoughts" tokens and the response `parts` may contain THOUGHT parts
  (``part.thought == true``). We set a GENEROUS ``maxOutputTokens`` (default
  8192 from settings), collect ONLY non-thought text parts, and handle
  ``finishReason == "MAX_TOKENS"`` WITHOUT raising (return partial text).
* Every outbound HTTP call goes through ``make_async_client()`` +
  ``with_retry()`` (>= 5 tries, exponential backoff) — the network is
  intermittent (TLS resets even when up).
* Embeddings (gemini-embedding-001) are requested at
  ``outputDimensionality=1536`` (Matryoshka) and L2-normalized; returned
  vectors always have length ``settings.embed_dim`` (1536).
* The API key is read ONLY from settings (``get_settings().gemini_api_key``);
  it is never hardcoded.
* JSON mode: when ``json_schema`` is provided we set
  ``responseMimeType="application/json"`` + ``responseSchema`` so the visible
  text parses as JSON.
"""

from __future__ import annotations

import json
import math
from typing import Any

from aidigest.config import Settings, get_settings
from aidigest.eval.rubric import criteria_names
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.llm.base import GenerationResult, JsonSchema, Message
from aidigest.obs.langfuse import get_tracer

# Embeddings are batched one-request-per-text (embedContent is single-content);
# generation is one request per prompt.
_EMBED_TASK_DEFAULT = "RETRIEVAL_DOCUMENT"


def _l2_normalize(vec: list[float], *, dim: int) -> list[float]:
    """Pad/truncate to ``dim`` then L2-normalize to a unit vector."""
    trimmed = list(vec[:dim])
    if len(trimmed) < dim:
        trimmed.extend([0.0] * (dim - len(trimmed)))
    norm = math.sqrt(sum(v * v for v in trimmed))
    if norm == 0.0:
        return trimmed
    return [v / norm for v in trimmed]


def _prompt_to_contents(prompt: str | list[Message]) -> tuple[list[dict], str | None]:
    """Convert a prompt into Gemini `contents` + an optional systemInstruction text.

    Gemini roles are "user"/"model"; "system" turns become a systemInstruction.
    """
    if isinstance(prompt, str):
        return [{"role": "user", "parts": [{"text": prompt}]}], None

    system_chunks: list[str] = []
    contents: list[dict] = []
    for msg in prompt:
        if msg.role == "system":
            system_chunks.append(msg.content)
            continue
        role = "model" if msg.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg.content}]})
    if not contents:
        contents = [{"role": "user", "parts": [{"text": ""}]}]
    system = "\n\n".join(system_chunks) if system_chunks else None
    return contents, system


def _prompt_preview(prompt: str | list[Message], *, limit: int = 4000) -> str:
    """A bounded plain-text preview of a prompt (for observability traces)."""
    text = prompt if isinstance(prompt, str) else "\n".join(m.content for m in prompt)
    return text[:limit]


def _extract_text(candidate: dict) -> str:
    """Collect ONLY non-thought text parts from a candidate."""
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if part.get("thought"):
            continue
        text = part.get("text")
        if text:
            chunks.append(text)
    return "".join(chunks)


def _usage_tokens(payload: dict) -> tuple[int, int, int]:
    """Return (prompt, output, thought) token counts from usageMetadata."""
    usage = payload.get("usageMetadata") or {}
    prompt = int(usage.get("promptTokenCount", 0) or 0)
    output = int(usage.get("candidatesTokenCount", 0) or 0)
    thought = int(usage.get("thoughtsTokenCount", 0) or 0)
    return prompt, output, thought


class GeminiClient:
    """Real Gemini REST client. Structurally satisfies ``LLMClient``."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.model = self._settings.gemini_model
        self.embed_model = self._settings.gemini_embed_model
        self.embed_dim = self._settings.embed_dim
        if not self._settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is empty. Set it in .env or use AIDIGEST_LLM_MOCK=1."
            )

    # --------------------------------------------------------------- internals
    @property
    def _base_url(self) -> str:
        return self._settings.gemini_base_url

    @property
    def _key(self) -> str:
        return self._settings.gemini_api_key

    async def _post(self, url: str, body: dict) -> dict:
        """POST JSON with retry. The API key is sent in the ``x-goog-api-key``
        HEADER, never the URL query string, so it cannot leak into request logs."""
        headers = {"x-goog-api-key": self._key}

        async def _do() -> dict:
            async with make_async_client() as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]

        return await with_retry(_do)

    # ---------------------------------------------------------------- generate
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
        contents, system = _prompt_to_contents(prompt)
        gen_config: dict[str, Any] = {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
        }
        if json_schema is not None:
            gen_config["responseMimeType"] = "application/json"
            gen_config["responseSchema"] = _sanitize_schema(json_schema)

        body: dict[str, Any] = {"contents": contents, "generationConfig": gen_config}
        if system is not None:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{self._base_url}/models/{self.model}:generateContent"
        payload = await self._post(url, body)

        candidates = payload.get("candidates") or []
        text = ""
        finish_reason = ""
        if candidates:
            text = _extract_text(candidates[0])
            finish_reason = candidates[0].get("finishReason", "") or ""

        prompt_tok, output_tok, thought_tok = _usage_tokens(payload)
        get_tracer().generation(
            name="gemini.generate",
            model=self.model,
            prompt=_prompt_preview(prompt),
            output=text,
            usage={
                "prompt_tokens": prompt_tok,
                "output_tokens": output_tok,
                "thought_tokens": thought_tok,
            },
            metadata={"json_mode": json_schema is not None, "finish_reason": finish_reason},
        )
        return GenerationResult(
            text=text,
            truncated=finish_reason == "MAX_TOKENS",
            prompt_tokens=prompt_tok,
            output_tokens=output_tok,
            thought_tokens=thought_tok,
            model=self.model,
        )

    # ------------------------------------------------------------------- embed
    async def embed(
        self,
        texts: list[str],
        *,
        task_type: str = _EMBED_TASK_DEFAULT,
    ) -> list[list[float]]:
        """Embed each text via embedContent (1536-dim, L2-normalized)."""
        vectors: list[list[float]] = []
        for text in texts:
            body = {
                "model": f"models/{self.embed_model}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.embed_dim,
                "taskType": task_type,
            }
            url = f"{self._base_url}/models/{self.embed_model}:embedContent"
            payload = await self._post(url, body)
            raw = (payload.get("embedding") or {}).get("values") or []
            vectors.append(_l2_normalize([float(v) for v in raw], dim=self.embed_dim))
        return vectors

    # ------------------------------------------------------------------- judge
    async def judge(
        self,
        *,
        candidates: list[str],
        rubric: dict,
        context: str = "",
    ) -> dict:
        """LLM-as-judge in a separate generation pass with a strict JSON schema."""
        if not candidates:
            return {"winner": 0, "scores": [], "rationale": "no candidates"}

        criteria = list((rubric.get("criteria") or {}).keys()) or criteria_names()
        scale = rubric.get("scale") or {"min": 1, "max": 5}
        schema = _judge_schema(criteria, n=len(candidates))
        prompt = _judge_prompt(candidates, criteria, scale, context)

        text = await self.generate(prompt, temperature=0.0, json_schema=schema)
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = {}

        scores = parsed.get("scores")
        if not isinstance(scores, list) or len(scores) != len(candidates):
            scores = [{c: 0.0 for c in criteria} for _ in candidates]
        winner = parsed.get("winner")
        if not isinstance(winner, int) or not 0 <= winner < len(candidates):
            winner = _argmax_by_total(scores, criteria)
        rationale = parsed.get("rationale")
        if not isinstance(rationale, str):
            rationale = ""
        return {"winner": winner, "scores": scores, "rationale": rationale}


# --------------------------------------------------------------------------- #
# Judge helpers
# --------------------------------------------------------------------------- #


def _argmax_by_total(scores: list[dict], criteria: list[str]) -> int:
    totals = [sum(float(s.get(c, 0.0)) for c in criteria) for s in scores]
    return max(range(len(totals)), key=lambda i: totals[i]) if totals else 0


def _judge_schema(criteria: list[str], *, n: int) -> dict:
    score_props = {c: {"type": "number"} for c in criteria}
    return {
        "type": "object",
        "properties": {
            "winner": {"type": "integer"},
            "scores": {
                "type": "array",
                "minItems": n,
                "items": {
                    "type": "object",
                    "properties": score_props,
                    "required": list(criteria),
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["winner", "scores", "rationale"],
    }


def _judge_prompt(
    candidates: list[str], criteria: list[str], scale: dict, context: str
) -> str:
    lo, hi = scale.get("min", 1), scale.get("max", 5)
    crit_lines = "\n".join(f"- {c}" for c in criteria)
    cand_blocks = "\n\n".join(
        f"### Candidate {i}\n{cand}" for i, cand in enumerate(candidates)
    )
    ctx = f"\nContext:\n{context}\n" if context else ""
    return (
        "You are a strict editorial judge. Score each candidate on every "
        f"criterion ({lo}-{hi}), pick the best (index), and explain briefly.\n"
        f"Criteria:\n{crit_lines}\n{ctx}\n{cand_blocks}\n\n"
        "Return JSON: winner (int index), scores (one object per candidate in "
        "order), rationale (string). Be honest: do not reward manufactured "
        "importance; reward calling a quiet day quiet."
    )


def _sanitize_schema(schema: dict) -> dict:
    """Drop keys Gemini's responseSchema does not accept; keep the structural subset."""
    allowed = {
        "type",
        "properties",
        "items",
        "enum",
        "required",
        "nullable",
        "format",
        "description",
    }
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _sanitize_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _sanitize_schema(value)
        else:
            out[key] = value
    # Gemini wants UPPER_CASE types.
    if isinstance(out.get("type"), str):
        out["type"] = out["type"].upper()
    return out


__all__ = ["GeminiClient"]
