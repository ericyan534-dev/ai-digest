"""LLM-as-judge: weekly best-of-N selection + nightly digest grading.

Two entry points (both work in MOCK mode for tests, LIVE mode for real grading):

  * ``judge_candidates`` — thin wrapper over ``LLMClient.judge`` used by the
    weekly best-of-N flow to pick the winning editorial draft.
  * ``grade_digest`` — grades ONE rendered digest against the editorial rubric
    (``eval/rubric.py``) and enforces the quiet-day honesty HARD GATE: a digest
    that violates the flexibility principle is capped at
    ``QUIET_DAY_CHECK.violation_score_cap`` regardless of other criteria.

All scoring is on the rubric's 1..5 scale. Every LLM call goes through
``aidigest.llm.factory.get_llm`` so the deterministic mock and the real Gemini
client are interchangeable (tests force ``AIDIGEST_LLM_MOCK=1``).
"""

from __future__ import annotations

import json
import re

from aidigest.eval.rubric import (
    QUIET_DAY_CHECK,
    SCALE_MAX,
    SCALE_MIN,
    criteria_names,
    rubric,
    weighted_total,
)
from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm

# Phrases that honestly signal a quiet day (no manufactured importance). The
# generator/prompts emit "Quiet day — nothing major shipped" and close variants.
_QUIET_MARKERS: tuple[str, ...] = (
    "quiet day",
    "quiet week",
    "nothing major shipped",
    "nothing major",
    "no important paper",
    "no major release",
    "no breakthrough",
    "slow day",
    "slow news day",
    "uneventful",
)

# Words that scream manufactured importance / marketing inflation — used as a
# heuristic signal that a (supposedly) quiet digest is inflating minor items.
_INFLATION_MARKERS: tuple[str, ...] = (
    "revolutionary",
    "game-chang",
    "groundbreaking",
    "breakthrough",
    "massive leap",
    "paradigm shift",
    "stunning",
    "unprecedented",
)


def _clamp_score(value: float) -> float:
    """Clamp a single-criterion score into the rubric's [SCALE_MIN, SCALE_MAX]."""
    return float(min(max(value, float(SCALE_MIN)), float(SCALE_MAX)))


def _looks_quiet(markdown: str) -> bool:
    """Heuristic: does the digest plainly admit a quiet day?"""
    low = markdown.lower()
    return any(marker in low for marker in _QUIET_MARKERS)


def _looks_inflated(markdown: str) -> bool:
    """Heuristic: does a (supposedly) quiet digest inflate minor items into hype?"""
    low = markdown.lower()
    return any(marker in low for marker in _INFLATION_MARKERS)


async def judge_candidates(
    candidates: list[str],
    *,
    context: str = "",
    llm: LLMClient | None = None,
) -> dict:
    """Pick the best editorial among ``candidates`` via the LLM-as-judge.

    Thin wrapper over ``LLMClient.judge(candidates=..., rubric=rubric(),
    context=...)``. Returns the judge dict shaped as::

        {"winner": int, "scores": [ {<criterion>: float, ...}, ... ], "rationale": str}

    ``winner`` is always a valid index into ``candidates`` (clamped). Empty
    candidate lists return a stable, harmless default rather than raising.
    """
    client = llm or get_llm()
    if not candidates:
        return {"winner": 0, "scores": [], "rationale": "no candidates"}

    result = await client.judge(candidates=candidates, rubric=rubric(), context=context)

    winner = int(result.get("winner", 0))
    winner = max(0, min(winner, len(candidates) - 1))
    scores = result.get("scores") or []
    rationale = str(result.get("rationale", ""))
    return {"winner": winner, "scores": list(scores), "rationale": rationale}


def _grade_schema() -> dict:
    """JSON schema requesting one 1..5 score per rubric criterion."""
    props = {
        name: {"type": "number"} for name in criteria_names()
    }
    return {"type": "object", "properties": props}


def _coerce_scores(raw: object) -> dict[str, float]:
    """Coerce an LLM/JSON payload into {criterion: clamped float} for every criterion."""
    parsed: dict = raw if isinstance(raw, dict) else {}
    scores: dict[str, float] = {}
    for name in criteria_names():
        value = parsed.get(name, float(SCALE_MIN))
        try:
            scores[name] = _clamp_score(float(value))
        except (TypeError, ValueError):
            scores[name] = float(SCALE_MIN)
    return scores


def _parse_json_scores(text: str) -> dict[str, float]:
    """Best-effort parse of a JSON score object from raw model text."""
    try:
        return _coerce_scores(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        # Try to salvage the first JSON object embedded in the text.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return _coerce_scores(json.loads(match.group(0)))
            except (json.JSONDecodeError, TypeError):
                pass
    return _coerce_scores(None)


def _quiet_gate(
    *,
    digest_markdown: str,
    quiet_expected: bool,
) -> tuple[bool, str]:
    """Apply the quiet-day honesty HARD GATE.

    Returns ``(quiet_ok, note)``. ``quiet_ok`` is False when the digest violates
    the flexibility principle:

      * quiet day expected but the digest does NOT say so plainly, OR
      * quiet day expected and the digest inflates minor items into hype.

    When ``quiet_expected`` is False (real news happened) the gate passes — depth
    of breakthrough coverage is graded by the normal criteria, not this gate.
    """
    if not quiet_expected:
        return True, "non-quiet day: honesty gate not applicable"

    said_quiet = _looks_quiet(digest_markdown)
    inflated = _looks_inflated(digest_markdown)
    if said_quiet and not inflated:
        return True, "quiet day acknowledged honestly"
    if not said_quiet:
        return False, "quiet day not acknowledged (no honest 'quiet day' signal)"
    return False, "quiet day inflated with manufactured-importance language"


async def grade_digest(
    digest_markdown: str,
    *,
    quiet_expected: bool,
    llm: LLMClient | None = None,
) -> dict:
    """Grade ONE rendered digest against the editorial rubric.

    Asks the LLM for a 1..5 score per criterion, then enforces the quiet-day
    honesty gate from ``rubric.QUIET_DAY_CHECK``: if the digest violates the
    flexibility principle, the weighted total is capped at
    ``violation_score_cap`` regardless of the per-criterion scores.

    Returns::

        {
          "scores": {<criterion>: float, ...},   # per-criterion, 1..5
          "total": float,                          # weighted aggregate (capped)
          "quiet_ok": bool,                        # honesty gate result
          "notes": str,
        }
    """
    client = llm or get_llm()

    prompt = (
        "You are an exacting editorial judge for a personal AI-news digest.\n"
        "Score the digest below from 1 (poor) to 5 (excellent) on each rubric "
        "criterion. Return ONLY a JSON object whose keys are the criteria "
        f"({', '.join(criteria_names())}) and values are numbers 1..5.\n"
        f"Rubric: {json.dumps(rubric()['criteria'])}\n"
        f"Quiet-day expected: {quiet_expected}.\n"
        "Honesty matters: a quiet day MUST be called quiet; a breakthrough MUST "
        "be covered at full depth.\n\n"
        "DIGEST:\n"
        f"{digest_markdown}\n"
    )

    raw = await client.generate(prompt, json_schema=_grade_schema(), temperature=0.0)
    scores = _parse_json_scores(raw)

    quiet_ok, gate_note = _quiet_gate(
        digest_markdown=digest_markdown, quiet_expected=quiet_expected
    )

    total = weighted_total(scores)
    cap = float(QUIET_DAY_CHECK["violation_score_cap"])
    notes = gate_note
    if not quiet_ok and total > cap:
        total = cap
        notes = f"{gate_note}; total capped at {cap} per quiet-day honesty gate"

    return {
        "scores": scores,
        "total": round(total, 4),
        "quiet_ok": quiet_ok,
        "notes": notes,
    }


__all__ = ["judge_candidates", "grade_digest"]
