"""Golden-set eval gate (mock mode): the flexibility principle holds end-to-end.

Deterministic regression gate for generator/prompt changes — runs in `make test`.
The live, score-floored version is `scripts/eval_gate.py` (`make eval`).
"""

from __future__ import annotations

import pytest

from aidigest.eval.gate import evaluate_daily, gate_failures


@pytest.mark.asyncio
async def test_eval_gate_quiet_set_is_honest(quiet_stories, quiet_items, profile) -> None:
    items_by_id = {it.id: it for it in quiet_items}
    result = await evaluate_daily(
        quiet_stories, items_by_id, profile=profile, quiet_expected=True
    )
    assert result["quiet_day"] is True
    assert result["quiet_ok"] is True
    assert gate_failures(result, quiet_expected=True) == []


@pytest.mark.asyncio
async def test_eval_gate_busy_set_surfaces_breakthrough(
    busy_stories, busy_items, profile
) -> None:
    items_by_id = {it.id: it for it in busy_items}
    result = await evaluate_daily(
        busy_stories, items_by_id, profile=profile, quiet_expected=False
    )
    assert result["quiet_day"] is False
    assert result["has_breakthrough"] is True
    assert gate_failures(result, quiet_expected=False) == []


@pytest.mark.asyncio
async def test_gate_failures_catches_mismatch(quiet_stories, quiet_items, profile) -> None:
    # A quiet result wrongly graded as a busy day MUST be flagged (regression guard
    # that the gate isn't a rubber stamp).
    items_by_id = {it.id: it for it in quiet_items}
    result = await evaluate_daily(
        quiet_stories, items_by_id, profile=profile, quiet_expected=True
    )
    assert gate_failures(result, quiet_expected=False)  # non-empty => caught
