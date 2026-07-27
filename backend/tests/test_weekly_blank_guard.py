"""Regression tests for the BLANK weekly digest shipped on 2026-07-26.

Production failure (GitHub Actions run 30209443924): every delivered byte of the
weekly email was its title line and its tier line — no lede, no body, no
shortlist, no radar. gemini-3.5-flash is a reasoning model that spends "thoughts"
tokens from the same ``maxOutputTokens`` budget, and the weekly editorial is the
longest single generation in the system. At the hardcoded 8192-token budget the
response came back ``finishReason=MAX_TOKENS``, so the truncated JSON parsed to
``{}`` for both the polish output and the winning candidate, and every field fell
through to its empty default — yet it was still emailed.

These tests pin the three defenses: a long-form token budget, a fallback chain
that prefers any intact draft, and a grounded body that is never empty.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.generate.weekly import (
    _LONG_FORM_MAX_OUTPUT_TOKENS,
    _fallback_body,
    _first_usable,
    _parse_entries,
    generate_weekly,
)
from aidigest.llm.base import GenerationResult, JsonSchema, Message
from aidigest.models import Family, Item, Story

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)

_TRUNCATED = '{"title": "Week at a Gla'  # what MAX_TOKENS actually returns
_INTACT = (
    '{"title": "The week compute got cheap", "lede": "A real lede.", '
    '"body_markdown": "A real editorial body.", "shortlist": [], "on_my_radar": []}'
)

PROFILE: dict = {"ranking": {"alpha": 0.5, "beta": 0.4, "gamma": 0.1}}


class RecordingLLM:
    """Fake client that records every call and replays scripted raw responses."""

    model = "fake-model"

    def __init__(
        self,
        *,
        candidate_texts: list[str] | None = None,
        polish_text: str = _TRUNCATED,
        candidate_error: type[Exception] | None = None,
        failing_indexes: set[int] | None = None,
    ) -> None:
        self._candidate_texts = candidate_texts
        self._polish_text = polish_text
        self._candidate_error = candidate_error
        self._failing_indexes = failing_indexes or set()
        self.budgets: list[int | None] = []
        self.candidate_calls = 0
        self.polish_calls = 0
        self.judge_calls = 0

    async def generate(self, prompt: str | list[Message], **kwargs: object) -> str:
        result = await self.generate_detailed(prompt, **kwargs)
        return result.text

    async def generate_detailed(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> GenerationResult:
        self.budgets.append(max_output_tokens)
        text = "\n".join(m.content for m in prompt) if isinstance(prompt, list) else prompt
        if "polish" in text.lower() or "winning" in text.lower():
            self.polish_calls += 1
            return GenerationResult(text=self._polish_text, truncated=True)
        index = self.candidate_calls
        self.candidate_calls += 1
        if self._candidate_error is not None and index in self._failing_indexes:
            raise self._candidate_error("simulated transport failure")
        if self._candidate_texts is not None:
            return GenerationResult(text=self._candidate_texts[index], truncated=False)
        return GenerationResult(text=_TRUNCATED, truncated=True)

    async def judge(self, *, candidates: list[str], rubric: dict, context: str = "") -> dict:
        self.judge_calls += 1
        return {"winner": 0, "scores": [], "rationale": ""}


def _item(iid: str, title: str, *, url: str | None) -> Item:
    return Item(
        id=iid,
        source="arxiv",
        family=Family.ACADEMIA,
        url=url,
        title=title,
        raw_text=f"body for {title}",
        published_at=_NOW,
        fetched_at=_NOW,
    )


def _story(sid: str, title: str, item: Item, *, score: float = 0.6) -> Story:
    return Story(
        id=sid,
        title=title,
        family=item.family,
        item_ids=[item.id],
        representative_item_id=item.id,
        final_rank=score,
        importance=score,
        mention_count=2,
        created_at=_NOW,
    )


@pytest.fixture
def week() -> tuple[list[Story], dict[str, Item]]:
    linked = _item("i1", "Sparse attention at scale", url="https://arxiv.org/abs/1")
    unlinked = _item("i2", "Unlinked lab announcement", url=None)
    stories = [
        _story("s1", "Sparse attention at scale", linked, score=0.7),
        _story("s2", "Unlinked lab announcement", unlinked, score=0.5),
    ]
    return stories, {linked.id: linked, unlinked.id: unlinked}


# --------------------------------------------------------------------------- #
# The budget that would have prevented the incident
# --------------------------------------------------------------------------- #


async def test_weekly_requests_the_long_form_token_budget(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """Every long-form weekly call must ask for the raised budget, not the default.

    This is the test that would have caught the original bug: at the implicit
    8192 default the response truncated and the digest shipped blank.
    """
    stories, items_by_id = week
    llm = RecordingLLM(candidate_texts=[_INTACT] * 3, polish_text=_INTACT)
    await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert llm.budgets, "no generation calls were made"
    assert all(b == _LONG_FORM_MAX_OUTPUT_TOKENS for b in llm.budgets)
    # Pinned against the value that actually truncated in production, so lowering
    # the constant back toward the old implicit default fails here.
    assert _LONG_FORM_MAX_OUTPUT_TOKENS > 8192
    assert llm.candidate_calls == 3 and llm.polish_calls == 1


# --------------------------------------------------------------------------- #
# Never ship a blank editorial
# --------------------------------------------------------------------------- #


async def test_all_drafts_truncated_still_yields_a_grounded_body(
    week: tuple[list[Story], dict[str, Item]], caplog: pytest.LogCaptureFixture
) -> None:
    """The exact production failure: nothing parses, so fall back to real stories."""
    stories, items_by_id = week
    llm = RecordingLLM()  # every response is truncated JSON
    with caplog.at_level("ERROR", logger="aidigest.generate"):
        digest = await generate_weekly(
            stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
        )
    assert digest.body_markdown.strip(), "shipped a blank weekly editorial"
    assert "Sparse attention at scale" in digest.body_markdown
    assert "Unlinked lab announcement" in digest.body_markdown
    # An operator must be able to find this in the Actions log.
    assert any("no parseable JSON" in r.message for r in caplog.records)


async def test_intact_candidate_beats_the_deterministic_fallback(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """A truncated polish must not discard a sibling draft that is whole."""
    stories, items_by_id = week
    llm = RecordingLLM(
        candidate_texts=[_TRUNCATED, _TRUNCATED, _INTACT], polish_text=_TRUNCATED
    )
    digest = await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.title == "The week compute got cheap"
    assert digest.body_markdown == "A real editorial body."
    assert "editorial pass failed" not in digest.body_markdown


async def test_quiet_week_says_so_instead_of_listing_stories(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """A genuinely quiet week stays honest rather than padding with a story dump."""
    stories, items_by_id = week
    quiet = [s.model_copy(update={"importance": 0.01, "final_rank": 0.01}) for s in stories]
    llm = RecordingLLM()
    digest = await generate_weekly(
        quiet, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.quiet_week is True
    assert digest.body_markdown == "Quiet week — nothing major shipped."


async def test_no_stories_at_all_does_not_crash() -> None:
    llm = RecordingLLM()
    digest = await generate_weekly(
        [], {}, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.body_markdown.strip()


# --------------------------------------------------------------------------- #
# One failed candidate must not take the week down
# --------------------------------------------------------------------------- #


async def test_one_failing_candidate_does_not_abort_the_weekly(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    stories, items_by_id = week
    llm = RecordingLLM(
        candidate_texts=[_INTACT, _INTACT, _INTACT],
        polish_text=_INTACT,
        candidate_error=RuntimeError,
        failing_indexes={1},
    )
    digest = await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.body_markdown == "A real editorial body."


async def test_every_candidate_failing_skips_judge_and_polish(
    week: tuple[list[Story], dict[str, Item]], caplog: pytest.LogCaptureFixture
) -> None:
    """No IndexError on an empty candidate list, and no pointless downstream calls."""
    stories, items_by_id = week
    llm = RecordingLLM(candidate_error=RuntimeError, failing_indexes={0, 1, 2})
    with caplog.at_level("ERROR", logger="aidigest.generate"):
        digest = await generate_weekly(
            stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
        )
    assert llm.judge_calls == 0 and llm.polish_calls == 0
    assert digest.body_markdown.strip()
    assert any("every one of" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Unit-level helpers
# --------------------------------------------------------------------------- #


def test_first_usable_prefers_a_draft_that_carries_a_body() -> None:
    assert _first_usable([_TRUNCATED, _INTACT], n_candidates=2)["title"] == (
        "The week compute got cheap"
    )


def test_first_usable_returns_a_bodyless_draft_rather_than_nothing() -> None:
    bodyless = '{"title": "Only a title"}'
    assert _first_usable([bodyless], n_candidates=1) == {"title": "Only a title"}


def test_first_usable_returns_empty_when_nothing_parses() -> None:
    assert _first_usable([_TRUNCATED, "also broken {"], n_candidates=2) == {}


def test_fallback_body_never_invents_a_link(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """GROUNDING: a story with no source URL renders as plain text, not a link."""
    stories, items_by_id = week
    body = _fallback_body(stories, items_by_id)
    assert "[Sparse attention at scale](https://arxiv.org/abs/1)" in body
    assert "[Unlinked lab announcement]" not in body
    assert "Unlinked lab announcement" in body


def test_fallback_body_with_no_stories_is_the_honest_quiet_line() -> None:
    assert _fallback_body([], {}) == "Quiet week — nothing major shipped."


# --------------------------------------------------------------------------- #
# Shortlist grounding: an empty source set must NOT disable URL validation
# --------------------------------------------------------------------------- #


def test_parse_entries_without_a_source_list_keeps_urls() -> None:
    rows = [{"title": "A paper", "url": "https://example.com/a", "one_liner": "x"}]
    assert _parse_entries(rows, None)[0].url == "https://example.com/a"


def test_parse_entries_with_an_empty_source_set_nulls_every_url() -> None:
    """Previously an empty set silently DISABLED the check, passing invented
    links straight to the reader whenever no items were loaded."""
    rows = [{"title": "A paper", "url": "https://invented.example/a", "one_liner": "x"}]
    entries = _parse_entries(rows, frozenset())
    assert entries[0].title == "A paper"
    assert entries[0].url is None


async def test_weekly_without_items_does_not_emit_invented_links() -> None:
    story = Story(
        id="s1",
        title="A story",
        family=Family.ACADEMIA,
        item_ids=["missing"],
        representative_item_id="missing",
        final_rank=0.6,
        importance=0.6,
        mention_count=2,
        created_at=_NOW,
    )
    shortlisted = (
        '{"title": "T", "lede": "L", "body_markdown": "B", '
        '"shortlist": [{"title": "A story", "url": "https://invented.example/x", '
        '"one_liner": "o", "family": "academia"}], "on_my_radar": []}'
    )
    llm = RecordingLLM(candidate_texts=[shortlisted] * 3, polish_text=shortlisted)
    digest = await generate_weekly(
        [story], {}, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.shortlist and all(e.url is None for e in digest.shortlist)
