"""Contract-layer tests: the foundation must be valid and stable.

These prove the binding pieces (models, mock LLM, config, rubric) work so
implementers build on solid ground. Implementers ADD their own tests; they
should not need to change these.
"""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from aidigest.eval.rubric import criteria_names, rubric, weighted_total
from aidigest.llm.base import LLMClient
from aidigest.llm.mock import MockLLMClient
from aidigest.models import (
    DailyDigest,
    DigestKind,
    Family,
    ImportanceTier,
    Item,
    content_hash,
    slugify,
)


def test_item_content_hash_is_deterministic() -> None:
    a = Item.create(source="hn", family=Family.COMMUNITY, title="X", url="https://e/1")
    b = Item.create(source="hn", family=Family.COMMUNITY, title="X", url="https://e/1")
    assert a.id == b.id
    assert len(a.id) == 64


def test_item_is_immutable_and_copy_updates() -> None:
    it = Item.create(source="hn", family=Family.COMMUNITY, title="X")
    it2 = it.with_embedding([0.0] * 1536)
    assert it.embedding is None
    assert it2.embedding is not None and len(it2.embedding) == 1536
    with pytest.raises(ValidationError):
        it.title = "Y"  # type: ignore[misc]


def test_content_hash_and_slugify() -> None:
    assert content_hash(url="https://E/1", text="x") == content_hash(url="https://e/1", text="y")
    assert slugify("DeepSeek V4: Release!") == "deepseek-v4-release"


def test_mock_is_llmclient() -> None:
    assert isinstance(MockLLMClient(), LLMClient)


@pytest.mark.asyncio
async def test_mock_embed_is_unit_1536_and_deterministic() -> None:
    llm = MockLLMClient(embed_dim=1536)
    v1 = (await llm.embed(["hello"]))[0]
    v2 = (await llm.embed(["hello"]))[0]
    assert len(v1) == 1536
    assert v1 == v2
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-9)


@pytest.mark.asyncio
async def test_mock_generate_json_schema() -> None:
    llm = MockLLMClient()
    schema = {
        "type": "object",
        "properties": {
            "tldr": {"type": "string"},
            "tier": {"type": "string", "enum": ["quiet_day", "notable"]},
        },
    }
    out = json.loads(await llm.generate("x", json_schema=schema))
    assert set(out) == {"tldr", "tier"}
    assert out["tier"] in {"quiet_day", "notable"}


@pytest.mark.asyncio
async def test_mock_judge_picks_winner() -> None:
    llm = MockLLMClient()
    res = await llm.judge(candidates=["a", "b", "c"], rubric=rubric())
    assert 0 <= res["winner"] < 3
    assert len(res["scores"]) == 3


def test_rubric_weights_sum_to_one() -> None:
    assert math.isclose(sum(m["weight"] for m in rubric()["criteria"].values()), 1.0)
    assert weighted_total({c: 5 for c in criteria_names()}) == 5.0


def test_daily_digest_quiet_day_shape() -> None:
    d = DailyDigest(
        id="daily-2026-06-21",
        date="2026-06-21",
        tldr="Quiet day — nothing major shipped.",
        overall_tier=ImportanceTier.QUIET_DAY,
        quiet_day=True,
    )
    assert d.kind == DigestKind.DAILY
    assert d.quiet_day is True
    # round-trips through JSON for the API layer
    assert DailyDigest.model_validate(d.model_dump(mode="json")) == d
