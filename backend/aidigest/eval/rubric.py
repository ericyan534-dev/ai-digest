"""The editorial rubric, as data.

Used by `eval/judge.py` (LLM-as-judge) for both the weekly best-of-N selection
and nightly digest grading. The five criteria are scored 1..5; the
quiet-day honesty check is a hard gate that can veto an otherwise-good digest.
"""

from __future__ import annotations

# Scoring scale shared by all criteria.
SCALE_MIN = 1
SCALE_MAX = 5

# Criterion -> (description, weight). Weights sum to 1.0.
CRITERIA: dict[str, dict] = {
    "insight": {
        "weight": 0.25,
        "description": (
            "Does it surface non-obvious connections and 'what everyone missed'? "
            "Synthesis over listing. Expert-level signal."
        ),
    },
    "accuracy": {
        "weight": 0.25,
        "description": (
            "Are claims faithful to the source items? No hallucinated results, "
            "numbers, or attributions."
        ),
    },
    "narrative": {
        "weight": 0.20,
        "description": (
            "Strong lede; stories connected into themes; consistent voice "
            "(fast-paced, dense, plain, lightly opinionated; no marketing adjectives)."
        ),
    },
    "personal_fit": {
        "weight": 0.20,
        "description": (
            "Tailored to the user's subfields (Multi-Agent Systems; Efficient & "
            "Scalable NLP; RL for NLP; LLMs & Foundation Models; Optimization) and "
            "venues (NeurIPS, ACL). 'Why it matters to you' is concrete, not generic."
        ),
    },
    "honesty": {
        "weight": 0.10,
        "description": (
            "Honors the flexibility principle: quiet days are called quiet, no "
            "manufactured importance; breakthroughs get full depth. No padding."
        ),
    },
}

# The quiet-day honesty check: a hard gate, not just a score.
QUIET_DAY_CHECK: dict = {
    "name": "quiet_day_honesty",
    "description": (
        "If the input contained no BREAKTHROUGH/NOTABLE stories, the digest MUST "
        "say so plainly (e.g. 'Quiet day — nothing major shipped') and MUST NOT "
        "inflate minor items into headlines. Conversely, a genuine breakthrough "
        "MUST be covered at full depth and not buried."
    ),
    # If violated, cap the overall score at this value regardless of other criteria.
    "violation_score_cap": 2.0,
}


def criteria_names() -> list[str]:
    """Ordered list of criterion keys (stable order for prompts/storage)."""
    return list(CRITERIA.keys())


def weighted_total(scores: dict[str, float]) -> float:
    """Compute the weighted aggregate (on the 1..5 scale) from per-criterion scores."""
    total = 0.0
    for name, meta in CRITERIA.items():
        total += float(scores.get(name, 0.0)) * float(meta["weight"])
    return round(total, 4)


def rubric() -> dict:
    """Return the full rubric as a single dict (passed to LLMClient.judge)."""
    return {
        "scale": {"min": SCALE_MIN, "max": SCALE_MAX},
        "criteria": CRITERIA,
        "quiet_day_check": QUIET_DAY_CHECK,
    }


__all__ = [
    "SCALE_MIN",
    "SCALE_MAX",
    "CRITERIA",
    "QUIET_DAY_CHECK",
    "criteria_names",
    "weighted_total",
    "rubric",
]
