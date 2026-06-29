"""Tests for personalize.profile and personalize.feedback (mock LLM, offline)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from aidigest.llm.mock import MockLLMClient
from aidigest.models import (
    Family,
    Feedback,
    FeedbackSignal,
    FeedbackTargetKind,
    Story,
)
from aidigest.personalize.feedback import (
    apply_nl_instruction,
    feedback_boosts,
    recompute_interest_vector,
)
from aidigest.personalize.profile import (
    build_interest_vector,
    load_profile,
    profile_text,
)

_NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def profile() -> dict:
    return load_profile()


@pytest.fixture
def llm() -> MockLLMClient:
    return MockLLMClient(embed_dim=1536)


def _is_unit(vec: list[float]) -> bool:
    return math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-6)


# --------------------------------------------------------------------------- #
# profile.py
# --------------------------------------------------------------------------- #


def test_load_profile_has_required_keys(profile: dict) -> None:
    assert "subfields" in profile
    assert "ranking" in profile
    assert "Multi-Agent Systems" in profile["subfields"]


def test_load_profile_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_profile("/nonexistent/profile.yaml")


def test_profile_text_includes_subfields_and_venues(profile: dict) -> None:
    text = profile_text(profile)
    assert "Multi-Agent Systems" in text
    assert "NeurIPS" in text


def test_profile_text_never_empty() -> None:
    assert profile_text({}).strip() != ""


async def test_build_interest_vector_is_unit_1536(profile: dict, llm: MockLLMClient) -> None:
    vec = await build_interest_vector(profile, llm=llm)
    assert len(vec) == 1536
    assert _is_unit(vec)


# --------------------------------------------------------------------------- #
# feedback.py — boosts
# --------------------------------------------------------------------------- #


def test_feedback_boosts_up_positive_down_negative() -> None:
    fbs = [
        Feedback(
            target_id="s1",
            target_kind=FeedbackTargetKind.STORY,
            signal=FeedbackSignal.UP,
            created_at=_NOW,
        ),
        Feedback(
            target_id="s2",
            target_kind=FeedbackTargetKind.STORY,
            signal=FeedbackSignal.DOWN,
            created_at=_NOW,
        ),
    ]
    boosts = feedback_boosts(fbs)
    assert boosts["s1"] > 0
    assert boosts["s2"] < 0


def test_feedback_boosts_time_decays() -> None:
    recent = Feedback(
        target_id="s1",
        target_kind=FeedbackTargetKind.STORY,
        signal=FeedbackSignal.UP,
        created_at=datetime.now(UTC),
    )
    old = Feedback(
        target_id="s2",
        target_kind=FeedbackTargetKind.STORY,
        signal=FeedbackSignal.UP,
        created_at=datetime.now(UTC) - timedelta(days=28),
    )
    boosts = feedback_boosts([recent, old], half_life_days=14.0)
    assert boosts["s1"] > boosts["s2"]
    assert boosts["s2"] == pytest.approx(0.25, abs=0.05)  # two half-lives ~ 0.25


def test_feedback_boosts_ignores_nl_instruction() -> None:
    fbs = [
        Feedback(
            target_id="profile",
            target_kind=FeedbackTargetKind.DIGEST,
            signal=FeedbackSignal.NL_INSTRUCTION,
            text="more academia",
            created_at=_NOW,
        )
    ]
    assert feedback_boosts(fbs) == {}


# --------------------------------------------------------------------------- #
# feedback.py — recompute interest vector (Loop 2)
# --------------------------------------------------------------------------- #


class _FakeRepo:
    """Minimal repo stub satisfying the two methods recompute needs."""

    def __init__(self, feedback: list[Feedback], stories: list[Story]) -> None:
        self._feedback = feedback
        self._stories = {s.id: s for s in stories}

    async def get_feedback(self, **_: object) -> list[Feedback]:
        return self._feedback

    async def get_stories_by_ids(self, ids: list[str]) -> list[Story]:
        return [self._stories[i] for i in ids if i in self._stories]


async def test_recompute_no_feedback_returns_base(profile: dict, llm: MockLLMClient) -> None:
    repo = _FakeRepo([], [])
    base = await build_interest_vector(profile, llm=llm)
    out = await recompute_interest_vector(repo, profile, llm=llm)  # type: ignore[arg-type]
    assert out == pytest.approx(base, abs=1e-9)


async def test_recompute_blends_and_stays_unit(profile: dict, llm: MockLLMClient) -> None:
    emb = (await llm.embed(["liked story about RL for NLP"]))[0]
    story = Story(
        id="liked",
        title="liked",
        family=Family.ACADEMIA,
        item_ids=["x"],
        embedding=emb,
        final_rank=0.8,
        created_at=_NOW,
    )
    fb = Feedback(
        target_id="liked",
        target_kind=FeedbackTargetKind.STORY,
        signal=FeedbackSignal.UP,
        created_at=datetime.now(UTC),
    )
    repo = _FakeRepo([fb], [story])
    out = await recompute_interest_vector(repo, profile, llm=llm)  # type: ignore[arg-type]
    assert len(out) == 1536
    assert _is_unit(out)


# --------------------------------------------------------------------------- #
# feedback.py — NL instruction (Loop 3)
# --------------------------------------------------------------------------- #


async def test_apply_nl_instruction_returns_profile_copy(profile: dict, llm: MockLLMClient) -> None:
    updated = await apply_nl_instruction("more academia, mute crypto", profile, llm=llm)
    assert updated is not profile
    # ranking weights remain clamped to [0,1]
    for key in ("alpha", "beta", "gamma"):
        assert 0.0 <= updated["ranking"][key] <= 1.0
    # original profile untouched (immutability of caller's dict structure intent)
    assert "ranking" in profile


async def test_apply_nl_instruction_empty_is_noop(profile: dict, llm: MockLLMClient) -> None:
    out = await apply_nl_instruction("   ", profile, llm=llm)
    assert out == profile
