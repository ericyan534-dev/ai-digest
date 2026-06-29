"""Tests for process/rank.py — importance + personal-fit blended ranking.

MOCK mode. Verifies the subfield-boost path (personal score = cosine to the
interest vector) actually changes ordering, and that feedback boosts shift rank.

Skipped until the PROCESS implementer lands ``aidigest.process.rank``.
"""

from __future__ import annotations

import math

import pytest

rank_mod = pytest.importorskip("aidigest.process.rank")
score_stories = rank_mod.score_stories
cosine = rank_mod.cosine
importance_score = rank_mod.importance_score
personal_score = rank_mod.personal_score

from aidigest.models import Family, Story  # noqa: E402


def _aligned(index: int, dim: int = 1536) -> list[float]:
    v = [0.0] * dim
    v[index] = 1.0
    return v


def test_cosine_basic() -> None:
    assert math.isclose(cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
    assert math.isclose(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
    assert math.isclose(cosine([1.0, 0.0], [-1.0, 0.0]), -1.0)


def test_cosine_handles_empty_or_zero() -> None:
    assert cosine([], []) == 0.0
    assert cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_personal_score_none_vector_is_zero() -> None:
    s = Story(id="s", title="t", family=Family.ACADEMIA, embedding=_aligned(0))
    assert personal_score(s, None) == 0.0


def test_subfield_boost_changes_ordering(profile: dict) -> None:
    """A story embedded ON the interest vector must out-rank an equally-important
    story that is orthogonal to it — that is the subfield boost.
    """
    interest = _aligned(0)
    on_subfield = Story(
        id="on", title="On subfield", family=Family.ACADEMIA,
        item_ids=["a"], representative_item_id="a", embedding=_aligned(0),
        importance=0.5, mention_count=3,
    )
    off_subfield = Story(
        id="off", title="Off subfield", family=Family.ACADEMIA,
        item_ids=["b"], representative_item_id="b", embedding=_aligned(500),
        importance=0.5, mention_count=3,
    )
    ranked = score_stories(
        [off_subfield, on_subfield], interest_vector=interest, profile=profile,
    )
    assert ranked[0].id == "on"
    assert ranked[0].personal > ranked[1].personal
    assert ranked[0].final_rank >= ranked[1].final_rank


def test_score_stories_sorted_desc(profile: dict) -> None:
    interest = _aligned(0)
    stories = [
        Story(id=f"s{i}", title=f"t{i}", family=Family.ACADEMIA,
              embedding=_aligned(i), importance=0.1 * i, mention_count=i + 1)
        for i in range(5)
    ]
    ranked = score_stories(stories, interest_vector=interest, profile=profile)
    ranks = [s.final_rank for s in ranked]
    assert ranks == sorted(ranks, reverse=True)


def test_score_stories_returns_new_objects(profile: dict) -> None:
    interest = _aligned(0)
    s = Story(id="s", title="t", family=Family.ACADEMIA, embedding=_aligned(0),
              importance=0.5)
    [scored] = score_stories([s], interest_vector=interest, profile=profile)
    assert scored is not s  # immutable copy
    assert s.final_rank == 0.0  # original untouched


def test_feedback_boost_lifts_a_story(profile: dict) -> None:
    interest = _aligned(0)
    a = Story(id="a", title="A", family=Family.ACADEMIA, embedding=_aligned(0),
              importance=0.5, mention_count=2)
    b = Story(id="b", title="B", family=Family.ACADEMIA, embedding=_aligned(0),
              importance=0.5, mention_count=2)
    base = score_stories([a, b], interest_vector=interest, profile=profile)
    base_rank = {s.id: s.final_rank for s in base}
    boosted = score_stories(
        [a, b], interest_vector=interest, profile=profile,
        feedback_boost={"b": 0.5},
    )
    boosted_rank = {s.id: s.final_rank for s in boosted}
    assert boosted_rank["b"] > base_rank["b"]
    assert boosted[0].id == "b"


def test_importance_score_rewards_mentions(profile: dict) -> None:
    low = Story(id="low", title="t", family=Family.ACADEMIA, mention_count=1)
    high = Story(id="high", title="t", family=Family.ACADEMIA, mention_count=10)
    assert importance_score(high, profile=profile) >= importance_score(low, profile=profile)
