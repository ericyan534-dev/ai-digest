"""End-to-end WEEKLY pipeline in MOCK mode: best-of-N + judge selection.

Exercises ``generate_weekly`` (which calls the harness's own
``eval.judge.judge_candidates`` for best-of-N selection) and asserts:

  * a Pydantic-valid ``WeeklyDigest`` that JSON-round-trips,
  * N candidates are generated and a winner is selected within range,
  * the winning candidate index is recorded on the digest,
  * shortlist + on_my_radar previews are populated from the story universe,
  * quiet-week input is handled honestly,
  * the run is idempotent (stable id).
"""

from __future__ import annotations

import pytest

from aidigest.eval.judge import judge_candidates
from aidigest.generate.weekly import generate_weekly
from aidigest.models import Item, WeeklyDigest


@pytest.mark.asyncio
async def test_weekly_is_valid_and_round_trips(
    busy_stories, busy_items: list[Item], llm, profile
) -> None:
    items_by_id = {it.id: it for it in busy_items}
    digest = await generate_weekly(
        busy_stories, items_by_id, profile=profile, week_of="2026-06-15",
        llm=llm, n_candidates=3,
    )
    assert isinstance(digest, WeeklyDigest)
    assert digest.id.startswith("weekly-")
    assert WeeklyDigest.model_validate(digest.model_dump(mode="json")) == digest
    assert digest.body_markdown  # has editorial body
    assert digest.lede


@pytest.mark.asyncio
async def test_weekly_best_of_n_judge_selects_winner(
    busy_stories, busy_items: list[Item], llm, profile
) -> None:
    items_by_id = {it.id: it for it in busy_items}
    n = 3
    digest = await generate_weekly(
        busy_stories, items_by_id, profile=profile, week_of="2026-06-15",
        llm=llm, n_candidates=n,
    )
    assert digest.candidate_count == n
    assert 0 <= digest.winning_candidate < n


@pytest.mark.asyncio
async def test_weekly_judge_is_deterministic_in_mock(
    busy_stories, busy_items: list[Item], llm, profile
) -> None:
    # The mock judge is seed-stable, so the same candidates pick the same winner.
    items_by_id = {it.id: it for it in busy_items}
    d1 = await generate_weekly(busy_stories, items_by_id, profile=profile,
                               week_of="2026-06-15", llm=llm, n_candidates=3)
    d2 = await generate_weekly(busy_stories, items_by_id, profile=profile,
                               week_of="2026-06-15", llm=llm, n_candidates=3)
    assert d1.id == d2.id
    assert d1.winning_candidate == d2.winning_candidate


@pytest.mark.asyncio
async def test_weekly_has_shortlist_and_radar(
    busy_stories, busy_items: list[Item], llm, profile
) -> None:
    items_by_id = {it.id: it for it in busy_items}
    digest = await generate_weekly(
        busy_stories, items_by_id, profile=profile, week_of="2026-06-15", llm=llm,
    )
    # "What I'd actually read" + "On my radar" previews exist (lists, possibly
    # empty on thin weeks but present as the contract fields).
    assert isinstance(digest.shortlist, list)
    assert isinstance(digest.on_my_radar, list)


@pytest.mark.asyncio
async def test_weekly_records_models(
    busy_stories, busy_items: list[Item], llm, profile
) -> None:
    items_by_id = {it.id: it for it in busy_items}
    digest = await generate_weekly(
        busy_stories, items_by_id, profile=profile, week_of="2026-06-15", llm=llm,
    )
    assert digest.model  # generating model id recorded
    assert digest.judge_model  # judge model id recorded


@pytest.mark.asyncio
async def test_weekly_quiet_week_handled(
    quiet_stories, quiet_items: list[Item], llm, profile
) -> None:
    items_by_id = {it.id: it for it in quiet_items}
    digest = await generate_weekly(
        quiet_stories, items_by_id, profile=profile, week_of="2026-06-15", llm=llm,
    )
    assert isinstance(digest, WeeklyDigest)
    assert digest.quiet_week is True


@pytest.mark.asyncio
async def test_judge_candidates_drives_selection(llm) -> None:
    # Direct check that the harness's judge_candidates (used inside the weekly
    # generator) returns a coherent winner for distinct drafts.
    res = await judge_candidates(
        ["The week reasoning got cheap.", "The week agents grew up.",
         "The week efficiency won."],
        context="week of 2026-06-15", llm=llm,
    )
    assert 0 <= res["winner"] < 3
    assert isinstance(res["rationale"], str)
