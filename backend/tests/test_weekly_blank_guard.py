"""Regression tests for the BLANK weekly digest shipped on 2026-07-26.

Production failure (Actions run 30209443924): the whole delivered email was a
title line and a tier line. The weekly asked for a long markdown editorial inside
a JSON string field; the generation degenerated, the JSON never closed, and every
field fell through to its empty default — yet it was still emailed.

Measured against the live API, the obvious fixes did not work: raising the budget
(the runaway expands to fill it; 32768 outran the HTTP timeout), bounding `title`
(the runaway relocates to `body_markdown`), and requiring every field (0 usable in
4 trials). What tracked the failure was prompt size — a dense ~15k-char story
block set failed 4 of 4, a diverse ~8k-char one succeeded 2 of 4.

So the editorial is now generated as PLAIN MARKDOWN, with a separate short-field
metadata call, and the story blocks are leaner. These tests pin that split plus
the degradation path that makes a failure survivable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from aidigest.generate.weekly import (
    _fallback_body,
    _first_nonempty,
    _lede_from_body,
    _parse_entries,
    _story_blocks,
    _strip_fences,
    _title_from_body,
    generate_weekly,
)
from aidigest.llm.base import GenerationResult, JsonSchema, Message
from aidigest.models import Family, Item, Story

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)

_EDITORIAL = (
    "# The week compute got cheap\n\n"
    "Sparse mixtures finally paid off this week.\n\n"
    "Two labs shipped models that trade parameters for routing.\n"
)
_META = json.dumps(
    {
        "title": "The week compute got cheap",
        "lede": "Sparse mixtures finally paid off this week.",
        "shortlist": [],
        "on_my_radar": [],
    }
)

PROFILE: dict = {"ranking": {"alpha": 0.5, "beta": 0.4, "gamma": 0.1}}


class RecordingLLM:
    """Fake client that records calls and replays scripted responses.

    Distinguishes the three call sites the way the real flow does: candidate and
    polish take NO json_schema (markdown), metadata takes one (JSON).
    """

    model = "fake-model"

    def __init__(
        self,
        *,
        candidate_texts: list[str] | None = None,
        polish_text: str = _EDITORIAL,
        metadata_text: str = _META,
        candidate_error: type[Exception] | None = None,
        failing_indexes: set[int] | None = None,
        metadata_error: type[Exception] | None = None,
    ) -> None:
        self._candidate_texts = candidate_texts
        self._polish_text = polish_text
        self._metadata_text = metadata_text
        self._candidate_error = candidate_error
        self._failing_indexes = failing_indexes or set()
        self._metadata_error = metadata_error
        self.schemas: list[JsonSchema] = []
        self.candidate_calls = 0
        self.polish_calls = 0
        self.metadata_calls = 0
        self.judge_calls = 0

    async def generate(self, prompt: str | list[Message], **kwargs: object) -> str:
        return (await self.generate_detailed(prompt, **kwargs)).text  # type: ignore[arg-type]

    async def generate_detailed(
        self,
        prompt: str | list[Message],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
        json_schema: JsonSchema = None,
    ) -> GenerationResult:
        self.schemas.append(json_schema)
        text = "\n".join(m.content for m in prompt) if isinstance(prompt, list) else prompt
        if json_schema is not None:  # metadata is the only JSON call
            self.metadata_calls += 1
            if self._metadata_error is not None:
                raise self._metadata_error("simulated metadata failure")
            return GenerationResult(text=self._metadata_text)
        if "polish" in text.lower():
            self.polish_calls += 1
            return GenerationResult(text=self._polish_text)
        index = self.candidate_calls
        self.candidate_calls += 1
        if self._candidate_error is not None and index in self._failing_indexes:
            raise self._candidate_error("simulated transport failure")
        if self._candidate_texts is not None:
            return GenerationResult(text=self._candidate_texts[index])
        return GenerationResult(text="", truncated=True)  # degenerate: nothing usable

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
        raw_text="x" * 4000,  # long enough that the weekly's tighter clip bites
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
    return (
        [
            _story("s1", "Sparse attention at scale", linked, score=0.7),
            _story("s2", "Unlinked lab announcement", unlinked, score=0.5),
        ],
        {linked.id: linked, unlinked.id: unlinked},
    )


# --------------------------------------------------------------------------- #
# The split: long text out of JSON, short fields in
# --------------------------------------------------------------------------- #


async def test_editorial_is_generated_as_markdown_not_json(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """The whole point of the restructure. A long markdown document inside a JSON
    string is what made this call unreliable — one degenerate run lost the entire
    object rather than a single field."""
    stories, items_by_id = week
    llm = RecordingLLM(candidate_texts=[_EDITORIAL] * 3)
    await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert llm.candidate_calls == 3 and llm.polish_calls == 1 and llm.metadata_calls == 1
    # Exactly one JSON call, and it is the metadata one.
    assert sum(s is not None for s in llm.schemas) == 1
    assert llm.schemas[-1] is not None


async def test_metadata_schema_carries_only_short_fields() -> None:
    from aidigest.generate.weekly import _METADATA_SCHEMA

    assert "body_markdown" not in _METADATA_SCHEMA["properties"]
    assert _METADATA_SCHEMA["properties"]["title"]["maxLength"] <= 300
    assert _METADATA_SCHEMA["properties"]["lede"]["maxLength"] <= 1000


def test_sanitize_schema_transmits_the_bounds() -> None:
    """Bounds are useless if the client strips them before the request."""
    from aidigest.generate.weekly import _METADATA_SCHEMA
    from aidigest.llm.gemini import _sanitize_schema

    sent = _sanitize_schema(_METADATA_SCHEMA)
    assert sent["properties"]["title"]["maxLength"] == 200
    assert sent["required"] == list(_METADATA_SCHEMA["properties"])
    assert sent["properties"]["shortlist"]["maxItems"] == 8


def test_weekly_story_blocks_are_leaner_than_the_daily_default(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """Prompt size is what tracked the degenerate generations. The weekly puts 20
    stories in ONE request, so it must not use the daily's 5x600-char budget."""
    from aidigest.generate._shared import sources_block

    stories, items_by_id = week
    weekly = _story_blocks(stories, items_by_id)
    daily_equivalent = sum(len(sources_block(s, items_by_id)) for s in stories)
    assert len(weekly) < daily_equivalent


# --------------------------------------------------------------------------- #
# Never ship a blank editorial
# --------------------------------------------------------------------------- #


async def test_all_drafts_empty_still_yields_a_grounded_body(
    week: tuple[list[Story], dict[str, Item]], caplog: pytest.LogCaptureFixture
) -> None:
    stories, items_by_id = week
    llm = RecordingLLM(polish_text="")  # every draft degenerate
    with caplog.at_level("ERROR", logger="aidigest.generate"):
        digest = await generate_weekly(
            stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
        )
    assert digest.body_markdown.strip(), "shipped a blank weekly editorial"
    assert "Sparse attention at scale" in digest.body_markdown
    assert any("no draft produced any text" in r.message for r in caplog.records)


async def test_intact_candidate_beats_the_deterministic_fallback(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    """An empty polish must not discard a sibling draft that is whole."""
    stories, items_by_id = week
    llm = RecordingLLM(candidate_texts=["", "", _EDITORIAL], polish_text="")
    digest = await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert "Sparse mixtures finally paid off" in digest.body_markdown
    assert "editorial pass failed" not in digest.body_markdown


async def test_quiet_week_says_so_instead_of_listing_stories(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    stories, items_by_id = week
    quiet = [s.model_copy(update={"importance": 0.01, "final_rank": 0.01}) for s in stories]
    llm = RecordingLLM(polish_text="")
    digest = await generate_weekly(
        quiet, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.quiet_week is True
    assert digest.body_markdown == "Quiet week — nothing major shipped."


async def test_no_stories_at_all_does_not_crash() -> None:
    llm = RecordingLLM(polish_text="")
    digest = await generate_weekly(
        [], {}, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.body_markdown.strip()


# --------------------------------------------------------------------------- #
# A failing call must not take the week down
# --------------------------------------------------------------------------- #


async def test_one_failing_candidate_does_not_abort_the_weekly(
    week: tuple[list[Story], dict[str, Item]],
) -> None:
    stories, items_by_id = week
    llm = RecordingLLM(
        candidate_texts=[_EDITORIAL] * 3,
        candidate_error=RuntimeError,
        failing_indexes={1},
    )
    digest = await generate_weekly(
        stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert "Sparse mixtures finally paid off" in digest.body_markdown


async def test_every_candidate_failing_skips_judge_and_polish(
    week: tuple[list[Story], dict[str, Item]], caplog: pytest.LogCaptureFixture
) -> None:
    stories, items_by_id = week
    llm = RecordingLLM(candidate_error=RuntimeError, failing_indexes={0, 1, 2})
    with caplog.at_level("ERROR", logger="aidigest.generate"):
        digest = await generate_weekly(
            stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
        )
    assert llm.judge_calls == 0 and llm.polish_calls == 0
    assert digest.body_markdown.strip()
    assert any("every one of" in r.message for r in caplog.records)


async def test_metadata_failure_still_ships_the_editorial(
    week: tuple[list[Story], dict[str, Item]], caplog: pytest.LogCaptureFixture
) -> None:
    """The editorial is the product. Losing its metadata must not lose the prose —
    title and lede come straight out of the markdown the model already wrote."""
    stories, items_by_id = week
    llm = RecordingLLM(candidate_texts=[_EDITORIAL] * 3, metadata_error=RuntimeError)
    with caplog.at_level("WARNING", logger="aidigest.generate"):
        digest = await generate_weekly(
            stories, items_by_id, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
        )
    assert digest.body_markdown == _EDITORIAL.strip()
    assert digest.title == "The week compute got cheap"
    assert digest.lede.startswith("Sparse mixtures finally paid off")
    assert any("metadata call failed" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_first_nonempty_skips_blanks() -> None:
    assert _first_nonempty(["", "   ", "real"]) == "real"
    assert _first_nonempty(["", ""]) == ""


def test_strip_fences_unwraps_a_code_fence() -> None:
    assert _strip_fences("```markdown\n# Title\n\nBody\n```") == "# Title\n\nBody"
    assert _strip_fences("# Title") == "# Title"


def test_title_and_lede_derive_from_the_markdown() -> None:
    assert _title_from_body(_EDITORIAL) == "The week compute got cheap"
    assert _lede_from_body(_EDITORIAL).startswith("Sparse mixtures finally paid off")


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
    """An empty set previously DISABLED the check, passing invented links through."""
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
    meta = json.dumps(
        {
            "title": "T",
            "lede": "L",
            "shortlist": [
                {
                    "title": "A story",
                    "url": "https://invented.example/x",
                    "one_liner": "o",
                    "family": "academia",
                }
            ],
            "on_my_radar": [],
        }
    )
    llm = RecordingLLM(candidate_texts=[_EDITORIAL] * 3, metadata_text=meta)
    digest = await generate_weekly(
        [story], {}, profile=PROFILE, week_of="2026-07-20", llm=llm, judge_llm=llm
    )
    assert digest.shortlist and all(e.url is None for e in digest.shortlist)
