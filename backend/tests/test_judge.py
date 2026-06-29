"""Tests for the validation harness itself: eval/judge.py + golden fixtures.

All MOCK mode. Covers:
  * judge_candidates picks a valid winner and tolerates edge cases.
  * grade_digest returns per-criterion scores + weighted total.
  * the quiet-day honesty HARD GATE: honest quiet -> pass; silent or inflated
    quiet -> fail + score capped at QUIET_DAY_CHECK.violation_score_cap.
  * golden fixtures load and have the structure the harness depends on.
"""

from __future__ import annotations

import pytest

from aidigest.eval.golden import golden_items, load_golden
from aidigest.eval.judge import grade_digest, judge_candidates
from aidigest.eval.rubric import (
    QUIET_DAY_CHECK,
    SCALE_MAX,
    SCALE_MIN,
    criteria_names,
)

CAP = float(QUIET_DAY_CHECK["violation_score_cap"])


# --------------------------------------------------------------------------- #
# judge_candidates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_judge_candidates_picks_valid_winner(llm) -> None:
    res = await judge_candidates(["lede A", "lede B", "lede C"], context="week", llm=llm)
    assert 0 <= res["winner"] < 3
    assert isinstance(res["scores"], list)
    assert isinstance(res["rationale"], str)


@pytest.mark.asyncio
async def test_judge_candidates_empty_is_safe(llm) -> None:
    res = await judge_candidates([], llm=llm)
    assert res["winner"] == 0
    assert res["scores"] == []


@pytest.mark.asyncio
async def test_judge_candidates_single(llm) -> None:
    res = await judge_candidates(["only one"], llm=llm)
    assert res["winner"] == 0


@pytest.mark.asyncio
async def test_judge_candidates_winner_clamped_to_range(llm) -> None:
    # Even if a (hypothetical) judge returned an out-of-range index, the wrapper
    # must clamp it. Use a stub that returns a bad winner.
    class BadJudge:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def judge(self, *, candidates, rubric, context=""):
            return {"winner": 999, "scores": [{}], "rationale": "x"}

    res = await judge_candidates(["a", "b"], llm=BadJudge())  # type: ignore[arg-type]
    assert res["winner"] == 1


# --------------------------------------------------------------------------- #
# grade_digest — score shape
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_grade_digest_returns_every_criterion(llm) -> None:
    res = await grade_digest("A normal busy-day digest with real news.",
                             quiet_expected=False, llm=llm)
    assert set(res["scores"].keys()) == set(criteria_names())
    for v in res["scores"].values():
        assert float(SCALE_MIN) <= v <= float(SCALE_MAX)
    assert "total" in res and "quiet_ok" in res and "notes" in res


@pytest.mark.asyncio
async def test_grade_digest_non_quiet_gate_not_applicable(llm) -> None:
    res = await grade_digest("DeepSeek V4 ships. Real story, full depth.",
                             quiet_expected=False, llm=llm)
    assert res["quiet_ok"] is True
    assert res["total"] <= float(SCALE_MAX)


# --------------------------------------------------------------------------- #
# grade_digest — the quiet-day HONESTY HARD GATE
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_quiet_day_acknowledged_passes_gate(llm) -> None:
    md = "Quiet day — nothing major shipped. A couple of minor library bumps."
    res = await grade_digest(md, quiet_expected=True, llm=llm)
    assert res["quiet_ok"] is True


@pytest.mark.asyncio
async def test_quiet_day_silent_fails_gate_and_caps(llm) -> None:
    # Quiet expected, but the digest pretends there is big news and never admits
    # it was quiet -> gate fails, total capped at the violation cap.
    md = ("Today's headlines: a new model release dominates. Sweeping coverage "
          "of several stories with detailed takeaways and analysis.")
    res = await grade_digest(md, quiet_expected=True, llm=llm)
    assert res["quiet_ok"] is False
    assert res["total"] <= CAP


@pytest.mark.asyncio
async def test_quiet_day_inflated_fails_gate(llm) -> None:
    # Says nothing-quiet AND uses manufactured-importance language -> fail.
    md = "A revolutionary, groundbreaking breakthrough resets all expectations today!"
    res = await grade_digest(md, quiet_expected=True, llm=llm)
    assert res["quiet_ok"] is False
    assert res["total"] <= CAP


@pytest.mark.asyncio
async def test_quiet_day_says_quiet_but_inflates_fails_gate(llm) -> None:
    # Admits it's a quiet day yet still inflates a minor item with hype language.
    # This is the subtle violation: the honesty word is present but the body
    # manufactures importance -> must still fail the gate.
    md = ("Quiet day overall, but this revolutionary groundbreaking breakthrough "
          "changes everything you thought you knew.")
    res = await grade_digest(md, quiet_expected=True, llm=llm)
    assert res["quiet_ok"] is False
    assert "inflat" in res["notes"].lower()
    assert res["total"] <= CAP


@pytest.mark.asyncio
async def test_quiet_gate_cap_note_appears_when_score_is_reduced() -> None:
    # The cap note is only added when an otherwise-high total is actually pulled
    # down to the cap. Use a high-scoring stub so the reduction happens.
    import json

    class HighScorer:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            return json.dumps({c: 5.0 for c in criteria_names()})

    md = "Big sweeping coverage, lots of important releases, nothing quiet here."
    res = await grade_digest(md, quiet_expected=True, llm=HighScorer())  # type: ignore[arg-type]
    assert res["quiet_ok"] is False
    assert res["total"] <= CAP
    assert str(CAP) in res["notes"]


@pytest.mark.asyncio
async def test_grade_digest_high_score_capped_when_gate_violated() -> None:
    # Force high per-criterion scores via a stub LLM, then confirm the gate cap
    # still wins on a silent quiet-day digest.
    import json

    class HighScorer:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            return json.dumps({c: 5.0 for c in criteria_names()})

    md = "Massive day of releases and detailed coverage. No mention of it being slow."
    res = await grade_digest(md, quiet_expected=True, llm=HighScorer())  # type: ignore[arg-type]
    assert res["quiet_ok"] is False
    assert res["total"] == CAP  # 5.0 weighted would be 5.0; capped down to CAP


@pytest.mark.asyncio
async def test_grade_digest_high_score_not_capped_when_honest() -> None:
    import json

    class HighScorer:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            return json.dumps({c: 5.0 for c in criteria_names()})

    md = "Quiet day — nothing major shipped. Honest and short."
    res = await grade_digest(md, quiet_expected=True, llm=HighScorer())  # type: ignore[arg-type]
    assert res["quiet_ok"] is True
    assert res["total"] > CAP  # honest digest keeps its real (high) score


@pytest.mark.asyncio
async def test_grade_digest_tolerates_garbage_json() -> None:
    class Garbage:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            return "not json at all"

    res = await grade_digest("Quiet day — nothing major shipped.",
                             quiet_expected=True, llm=Garbage())  # type: ignore[arg-type]
    # Falls back to floor scores but does not crash.
    assert set(res["scores"].keys()) == set(criteria_names())
    assert res["quiet_ok"] is True


# --------------------------------------------------------------------------- #
# golden fixtures
# --------------------------------------------------------------------------- #


def test_golden_busy_day_structure() -> None:
    g = load_golden("busy_day")
    assert g["quiet_expected"] is False
    assert g["expected_overall_tier"] == "breakthrough"
    items = golden_items("busy_day")
    assert len(items) >= 5
    # cross-source breakthrough: DeepSeek mentioned by >= 3 distinct sources
    ds = [it for it in items if "deepseek" in it.title.lower()]
    assert len({it.source for it in ds}) >= 3


def test_golden_quiet_day_structure() -> None:
    g = load_golden("quiet_day")
    assert g["quiet_expected"] is True
    assert g["expected_overall_tier"] == "quiet_day"
    items = golden_items("quiet_day")
    assert 1 <= len(items) <= 6
    # nothing major: no item screams breakthrough in its title
    assert not any("deepseek" in it.title.lower() for it in items)


def test_golden_items_have_valid_families() -> None:
    for name in ("busy_day", "quiet_day"):
        for it in golden_items(name):
            assert it.family.value in {"academia", "industry", "community", "meta"}
            assert it.id and len(it.id) == 64


def test_golden_path_points_at_json() -> None:
    from aidigest.eval.golden import golden_path

    p = golden_path("busy_day")
    assert p.name == "busy_day.json"
    assert p.exists()


def test_golden_malformed_raises(tmp_path, monkeypatch) -> None:
    # A fixture without an 'items' key is rejected.
    import aidigest.eval.golden as golden_mod

    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "bad"}', encoding="utf-8")
    monkeypatch.setattr(golden_mod, "golden_path", lambda name: bad)
    with pytest.raises(ValueError):
        load_golden("bad")


def test_golden_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_golden("does_not_exist")


# --------------------------------------------------------------------------- #
# grade_digest robustness — malformed / wrapped LLM output paths
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_grade_digest_salvages_json_wrapped_in_prose() -> None:
    # Model wraps the JSON in chatter; the grader must still extract the scores.
    import json as _json

    class Wrapped:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            obj = {c: 4.0 for c in criteria_names()}
            return f"Sure! Here are the scores: {_json.dumps(obj)} — hope that helps."

    res = await grade_digest("DeepSeek V4 ships. Real news.",
                             quiet_expected=False, llm=Wrapped())  # type: ignore[arg-type]
    # All 4.0 -> weighted total 4.0 (gate not applicable on a non-quiet day).
    assert all(v == 4.0 for v in res["scores"].values())
    assert res["total"] == 4.0


@pytest.mark.asyncio
async def test_grade_digest_non_numeric_scores_floor() -> None:
    import json as _json

    class NonNumeric:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            # Values are strings that aren't numbers -> must floor, not crash.
            return _json.dumps({c: "high" for c in criteria_names()})

    res = await grade_digest("Quiet day — nothing major shipped.",
                             quiet_expected=True, llm=NonNumeric())  # type: ignore[arg-type]
    assert all(v == float(SCALE_MIN) for v in res["scores"].values())


@pytest.mark.asyncio
async def test_grade_digest_braces_but_invalid_json_floors() -> None:
    # Output has a {...} that the regex salvage path finds, but it is NOT valid
    # JSON -> the grader floors gracefully instead of crashing.
    class BracesGarbage:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            return "Here you go: {insight: five, accuracy: ???} done"

    res = await grade_digest("DeepSeek V4 ships. Real news.",
                             quiet_expected=False, llm=BracesGarbage())  # type: ignore[arg-type]
    assert all(v == float(SCALE_MIN) for v in res["scores"].values())


@pytest.mark.asyncio
async def test_grade_digest_out_of_range_scores_clamped() -> None:
    import json as _json

    class OutOfRange:
        model = "stub"
        embed_model = "stub"
        embed_dim = 1536

        async def generate(self, prompt, *, max_output_tokens=8192,
                           temperature=0.7, json_schema=None):
            return _json.dumps({c: 99.0 for c in criteria_names()})

    res = await grade_digest("DeepSeek V4 ships. Real news.",
                             quiet_expected=False, llm=OutOfRange())  # type: ignore[arg-type]
    assert all(v == float(SCALE_MAX) for v in res["scores"].values())
