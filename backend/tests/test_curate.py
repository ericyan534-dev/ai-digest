"""Editorial curator — parse + keep-the-worthy filtering (replaces relevance gate)."""

from __future__ import annotations

import json
import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402

import aidigest.process.curate as curate  # noqa: E402
from aidigest.config import Settings  # noqa: E402
from aidigest.models import Family, Story  # noqa: E402
from aidigest.process.curate import _parse_keep, curate_stories  # noqa: E402


class _FakeLLM:
    model = "fake"

    def __init__(self, keep: list[int]) -> None:
        self._keep = keep

    async def generate(self, prompt, *, json_schema=None, temperature=0.0, max_output_tokens=8192) -> str:  # type: ignore[no-untyped-def]
        return json.dumps({"keep": self._keep})


def _story(i: int, title: str, family: Family = Family.COMMUNITY) -> Story:
    return Story(
        id=f"s{i}", title=title, family=family,
        item_ids=[f"i{i}"], representative_item_id=f"i{i}",
    )


def _live() -> Settings:
    return Settings(AIDIGEST_LLM_MOCK=False, AIDIGEST_RELEVANCE_FILTER=True)  # type: ignore[call-arg]


def test_parse_keep() -> None:
    assert _parse_keep(
        '{"keep": [{"n":1,"family":"industry"},{"n":3,"family":"academia"}]}', n=3
    ) == [(0, "industry"), (2, "academia")]
    assert _parse_keep('{"keep": [1, 3]}', n=3) == [(0, None), (2, None)]  # legacy ints
    assert _parse_keep('{"keep": []}', n=3) == []
    assert _parse_keep("not json", n=3) is None
    assert _parse_keep('{"keep": [{"n":9},{"n":2}]}', n=3) == [(1, None)]  # out-of-range dropped


@pytest.mark.asyncio
async def test_curate_reassigns_topic_family(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curate, "get_settings", _live)
    stories = [
        _story(1, "Anthropic releases Claude Fable 5", Family.COMMUNITY),  # broke on HN
        _story(2, "New linear-attention paper", Family.ACADEMIA),
    ]
    out = await curate_stories(
        stories,
        profile={},
        llm=_FakeLLM([{"n": 1, "family": "industry"}, {"n": 2, "family": "academia"}]),
    )
    by_title = {s.title: s.family for s in out}
    assert by_title["Anthropic releases Claude Fable 5"] == Family.INDUSTRY  # reclassified
    assert by_title["New linear-attention paper"] == Family.ACADEMIA


@pytest.mark.asyncio
async def test_curate_noop_in_mock() -> None:
    stories = [_story(1, "a"), _story(2, "b")]
    assert await curate_stories(stories, profile={}) == stories


def test_is_self_promo() -> None:
    from aidigest.process.curate import _is_self_promo

    assert _is_self_promo(_story(1, "Show HN: my weekend GPT wrapper"))
    assert _is_self_promo(_story(2, "Kuma: compiling to WebGPU [P]"))
    assert not _is_self_promo(_story(3, "OpenAI unveils inference chip", Family.INDUSTRY))
    # Cross-source corroborated -> survives even with "Show HN" framing.
    corroborated = _story(4, "Show HN: big release").model_copy(update={"mention_count": 3})
    assert not _is_self_promo(corroborated)


def test_is_low_signal_noise() -> None:
    from aidigest.process.curate import _is_low_signal_noise

    # The exact viral-anecdote posts that must NEVER reach the digest.
    assert _is_low_signal_noise(_story(1, "I used Claude Code to get a second opinion on my MRI"))
    assert _is_low_signal_noise(
        _story(2, "HackerRank open sourced its ATS. My resume scored 90/100. Oh wait 74. No - 88")
    )
    # Real research/industry stories are untouched.
    assert not _is_low_signal_noise(_story(3, "GLM 5.2 beats Claude in our benchmarks"))
    assert not _is_low_signal_noise(_story(4, "OpenAI unveils GPT-5.6 Sol", Family.INDUSTRY))
    # Corroborated across sources -> survives even with an anecdotal keyword.
    corroborated = _story(5, "My resume parser beats the SOTA").model_copy(
        update={"mention_count": 3}
    )
    assert not _is_low_signal_noise(corroborated)


@pytest.mark.asyncio
async def test_curate_drops_low_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curate, "get_settings", _live)
    stories = [
        _story(1, "RL without ground-truth improves LLMs", Family.ACADEMIA),
        _story(2, "Show HN: my weekend GPT wrapper", Family.COMMUNITY),
        _story(3, "OpenAI unveils inference chip", Family.INDUSTRY),
    ]
    # The Show HN item is hard pre-filtered before the LLM; keep=[1,2] is over the
    # post-filter list (academia, industry).
    out = await curate_stories(stories, profile={"subfields": ["RL for NLP"]}, llm=_FakeLLM([1, 2]))
    titles = [s.title for s in out]
    assert "Show HN: my weekend GPT wrapper" not in titles
    assert "RL without ground-truth improves LLMs" in titles
    assert "OpenAI unveils inference chip" in titles
    assert len(out) == 2


@pytest.mark.asyncio
async def test_curate_parse_failure_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curate, "get_settings", _live)

    class _Bad:
        model = "bad"

        async def generate(self, *a, **k):  # type: ignore[no-untyped-def]
            return "no json here"

    stories = [_story(1, "a"), _story(2, "b")]
    assert await curate_stories(stories, profile={}, llm=_Bad()) == stories


# --------------------------------------------------------------------------- #
# Fix A Layer 2 — _is_meta_recap deterministic hard-drop
# --------------------------------------------------------------------------- #


def test_is_meta_recap_not_much_happened() -> None:
    """Classic quiet-day recap phrase is always dropped regardless of mention_count."""
    from aidigest.process.curate import _is_meta_recap

    s = _story(1, "not much happened today").model_copy(update={"mention_count": 2})
    assert _is_meta_recap(s) is True


def test_is_meta_recap_nothing_much_happened() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Nothing much happened this week")) is True


def test_is_meta_recap_quiet_day() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Quiet day in AI land")) is True


def test_is_meta_recap_quiet_weekend() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Quiet Weekend — no big launches")) is True


def test_is_meta_recap_slow_news() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Slow news day for AI")) is True


def test_is_meta_recap_ainews_headline() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "AINews: week in review")) is True


def test_is_meta_recap_weekly_recap() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Weekly Recap: best of AI")) is True


def test_is_meta_recap_weekly_roundup() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "The Weekly Roundup for AI researchers")) is True


def test_is_meta_recap_week_in_review() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Week in Review: LLMs dominate")) is True


def test_is_meta_recap_top_ai_news() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Top AI News of the week")) is True


def test_is_meta_recap_news_roundup() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "News Roundup: AI edition")) is True


def test_is_meta_recap_daily_digest() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Daily Digest: June 21")) is True


def test_is_meta_recap_daily_recap() -> None:
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Daily Recap — what you missed")) is True


# Adversarial near-misses that must NOT be dropped


def test_is_meta_recap_quiet_star_not_dropped() -> None:
    """'Quiet-STaR' starts with 'Quiet' but has no 'quiet day/week' phrase."""
    from aidigest.process.curate import _is_meta_recap

    s = _story(1, "Quiet-STaR: Language Models Can Teach Themselves to Think")
    assert _is_meta_recap(s) is False


def test_is_meta_recap_real_paper_not_dropped() -> None:
    """A real paper title containing 'news' must not false-positive."""
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "Breaking News Detection with Neural Networks")) is False


def test_is_meta_recap_release_not_dropped() -> None:
    """A concrete model release announcement is never dropped."""
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "OpenAI announces GPT-5.6 Sol")) is False


def test_is_meta_recap_dropped_regardless_of_mention_count() -> None:
    """Unlike self-promo, recap headlines are dropped EVEN when corroborated."""
    from aidigest.process.curate import _is_meta_recap

    # corroborated across 5 sources — still a recap
    s = _story(1, "AINews: Top AI Stories this week").model_copy(update={"mention_count": 5})
    assert _is_meta_recap(s) is True


@pytest.mark.asyncio
async def test_curate_drops_meta_recap(monkeypatch: pytest.MonkeyPatch) -> None:
    """curate_stories deterministic pass drops meta-recap even before the LLM."""
    monkeypatch.setattr(curate, "get_settings", _live)
    stories = [
        _story(1, "OpenAI releases GPT-5.6 Sol", Family.INDUSTRY),
        _story(2, "AINews: Week in Review").model_copy(update={"mention_count": 3}),
    ]
    # LLM would keep both (n=1 and n=2), but the recap must be pre-filtered.
    out = await curate_stories(
        stories, profile={}, llm=_FakeLLM([{"n": 1, "family": "industry"}, {"n": 2, "family": "meta"}])
    )
    titles = [s.title for s in out]
    assert "AINews: Week in Review" not in titles
    assert "OpenAI releases GPT-5.6 Sol" in titles


@pytest.mark.asyncio
async def test_curate_malformed_output_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    # The model occasionally concatenates every number into one giant out-of-range
    # integer; that must fall back to keeping all, NEVER an empty digest.
    monkeypatch.setattr(curate, "get_settings", _live)

    class _Concat:
        model = "concat"

        async def generate(self, *a, **k):  # type: ignore[no-untyped-def]
            return json.dumps({"keep": [{"n": 152134810112126282930323437465052}]})

    stories = [_story(1, "a"), _story(2, "b"), _story(3, "c")]
    out = await curate_stories(stories, profile={}, llm=_Concat())
    assert out == stories  # never nuked to empty


# --------------------------------------------------------------------------- #
# Fix 1 — ainews separator tightening (code-review round)
# --------------------------------------------------------------------------- #


def test_is_meta_recap_ainews_with_separator_dropped() -> None:
    """'AINews:' recap form (with separator) is still dropped."""
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "AINews: not much happened today")) is True


def test_is_meta_recap_ainews_without_separator_not_dropped() -> None:
    """A story ABOUT AINews (no separator) must NOT be dropped."""
    from aidigest.process.curate import _is_meta_recap

    assert _is_meta_recap(_story(1, "AINews raises $5M to expand coverage")) is False
