"""Tests for the real Gemini client (aidigest.llm.gemini) via a mocked transport.

No network, no real key. We inject an ``httpx.MockTransport`` into the shared
``make_async_client`` so we can assert the HARD REQUIREMENTS:
  * thought parts are stripped; only answer text returned,
  * finishReason MAX_TOKENS sets truncated without raising,
  * transient TLS resets are retried (>= 5 tries),
  * embeddings are 1536-dim, L2-normalized, requested at outputDimensionality=1536,
  * judge returns {winner, scores, rationale}.
"""

from __future__ import annotations

import json
import math
from unittest.mock import patch

import httpx
import pytest

from aidigest.config import Settings
from aidigest.eval.rubric import rubric
from aidigest.llm.base import LLMClient, Message
from aidigest.llm.gemini import (
    GeminiClient,
    _extract_text,
    _l2_normalize,
    _prompt_to_contents,
    _sanitize_schema,
)


def _settings() -> Settings:
    return Settings(GEMINI_API_KEY="test-key", AIDIGEST_LLM_MOCK=False)  # type: ignore[call-arg]


def _patched_client_factory(transport: httpx.MockTransport):
    import aidigest.ingest.base as base

    orig = base.make_async_client

    def patched(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig(**kwargs)

    return patched


def test_gemini_requires_key() -> None:
    with pytest.raises(RuntimeError):
        GeminiClient(settings=Settings(GEMINI_API_KEY="", AIDIGEST_LLM_MOCK=False))  # type: ignore[call-arg]


def test_gemini_is_llmclient() -> None:
    assert isinstance(GeminiClient(settings=_settings()), LLMClient)


def test_l2_normalize_pads_and_units() -> None:
    v = _l2_normalize([3.0, 4.0], dim=1536)
    assert len(v) == 1536
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)
    assert math.isclose(v[0], 0.6) and math.isclose(v[1], 0.8)


def test_l2_normalize_truncates() -> None:
    assert len(_l2_normalize([1.0] * 4096, dim=1536)) == 1536


def test_l2_normalize_zero_vector_is_safe() -> None:
    v = _l2_normalize([0.0, 0.0], dim=4)
    assert v == [0.0, 0.0, 0.0, 0.0]


def test_extract_text_skips_thoughts() -> None:
    cand = {
        "content": {
            "parts": [
                {"thought": True, "text": "secret thinking"},
                {"text": "visible "},
                {"text": "answer"},
            ]
        }
    }
    assert _extract_text(cand) == "visible answer"


def test_prompt_to_contents_extracts_system() -> None:
    contents, system = _prompt_to_contents(
        [
            Message(role="system", content="SYS"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="yo"),
        ]
    )
    assert system == "SYS"
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"


def test_sanitize_schema_uppercases_and_filters() -> None:
    out = _sanitize_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"t": {"type": "string", "enum": ["a", "b"]}},
            "required": ["t"],
        }
    )
    assert out["type"] == "OBJECT"
    assert "additionalProperties" not in out
    assert out["properties"]["t"]["type"] == "STRING"


@pytest.mark.asyncio
async def test_generate_strips_thoughts_and_handles_max_tokens() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("reset")  # transient -> retried
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {
                            "parts": [
                                {"thought": True, "text": "thinking"},
                                {"text": "partial"},
                            ]
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 9,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 50,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with patch("aidigest.llm.gemini.make_async_client", _patched_client_factory(transport)):
        client = GeminiClient(settings=_settings())
        res = await client.generate_detailed("hello")
    assert res.text == "partial"
    assert res.truncated is True
    assert res.thought_tokens == 50
    assert calls["n"] == 2  # retried past the reset


@pytest.mark.asyncio
async def test_embed_is_1536_normalized_and_requests_matryoshka() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embedding": {"values": [0.5] * 1536}})

    transport = httpx.MockTransport(handler)
    with patch("aidigest.llm.gemini.make_async_client", _patched_client_factory(transport)):
        client = GeminiClient(settings=_settings())
        vecs = await client.embed(["doc"], task_type="RETRIEVAL_DOCUMENT")
    assert len(vecs) == 1 and len(vecs[0]) == 1536
    assert math.isclose(math.sqrt(sum(x * x for x in vecs[0])), 1.0, rel_tol=1e-9)
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["outputDimensionality"] == 1536
    assert body["taskType"] == "RETRIEVAL_DOCUMENT"


@pytest.mark.asyncio
async def test_generate_json_mode_sets_response_schema() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]},
        )

    transport = httpx.MockTransport(handler)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    with patch("aidigest.llm.gemini.make_async_client", _patched_client_factory(transport)):
        client = GeminiClient(settings=_settings())
        out = await client.generate("x", json_schema=schema)
    assert json.loads(out) == {"ok": True}
    body = seen["body"]
    assert isinstance(body, dict)
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseSchema"]["type"] == "OBJECT"


@pytest.mark.asyncio
async def test_judge_returns_winner_scores_rationale() -> None:
    payload = {
        "winner": 1,
        "scores": [
            {"insight": 2, "accuracy": 3, "narrative": 2, "personal_fit": 2, "honesty": 3},
            {"insight": 5, "accuracy": 5, "narrative": 4, "personal_fit": 4, "honesty": 5},
        ],
        "rationale": "second is sharper",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]},
        )

    transport = httpx.MockTransport(handler)
    with patch("aidigest.llm.gemini.make_async_client", _patched_client_factory(transport)):
        client = GeminiClient(settings=_settings())
        res = await client.judge(candidates=["a", "b"], rubric=rubric())
    assert res["winner"] == 1
    assert len(res["scores"]) == 2
    assert res["rationale"] == "second is sharper"


@pytest.mark.asyncio
async def test_judge_empty_candidates() -> None:
    client = GeminiClient(settings=_settings())
    res = await client.judge(candidates=[], rubric=rubric())
    assert res["winner"] == 0
    assert res["scores"] == []
