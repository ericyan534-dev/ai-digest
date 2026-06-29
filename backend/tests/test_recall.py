"""'Did-I-miss-anything' recall eval — coverage metric + omission gate."""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402

from aidigest.eval.recall import recall_check, recall_gate  # noqa: E402
from aidigest.models import Family, Story  # noqa: E402


def _story(i: int, rank: float) -> Story:
    return Story(
        id=f"s{i}",
        title=f"Story {i}",
        family=Family.ACADEMIA,
        item_ids=[f"i{i}"],
        representative_item_id=f"i{i}",
        final_rank=rank,
        mention_count=1,
    )


@pytest.mark.asyncio
async def test_recall_all_covered_is_clean() -> None:
    stories = [_story(1, 0.9), _story(2, 0.8)]
    result = await recall_check(stories, ["s1", "s2"], top_k=5)
    assert result["coverage"] == 1.0
    assert result["missed_count"] == 0
    assert recall_gate(result, quiet_expected=False) == []


@pytest.mark.asyncio
async def test_recall_quiet_day_is_lenient() -> None:
    stories = [_story(1, 0.9), _story(2, 0.8)]
    result = await recall_check(stories, [], top_k=5, quiet_expected=True)
    # On a quiet day the LLM omission pass is skipped and the gate never fails.
    assert result["missed_count"] == 0
    assert recall_gate(result, quiet_expected=True) == []


@pytest.mark.asyncio
async def test_recall_busy_uncovered_flags_omissions() -> None:
    stories = [_story(1, 0.9), _story(2, 0.8), _story(3, 0.7)]
    result = await recall_check(stories, ["s1"], top_k=5)
    assert result["coverage"] < 1.0
    # The (deterministic) mock judge returns a non-empty missed list.
    assert result["missed_count"] >= 1
    assert recall_gate(result, quiet_expected=False)
