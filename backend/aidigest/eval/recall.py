"""'Did-I-miss-anything' recall eval — the highest-stakes omission check.

The worst failure for a personal digest is dropping a story that mattered. This
scores how well a generated digest covered the day's high-rank stories:

  * COVERAGE — of the top-K ranked stories, the fraction the digest actually
    included (computed from ranks: deterministic, no LLM, reproducible).
  * an LLM OMISSION pass over the NOT-covered top stories: "which of these, if
    any, was important enough that dropping it hurts a reader who tracks
    LLMs / RL / efficiency / multi-agent research?" (qualitative, mock-safe).

``recall_gate`` turns the verdict into pass/fail messages for a deploy gate. On a
quiet day omission is expected, so the gate is lenient.
"""

from __future__ import annotations

import json
import re

from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm
from aidigest.models import Story

DEFAULT_TOP_K = 10
DEFAULT_COVERAGE_FLOOR = 0.7

_MISSED_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "missed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        }
    },
}


def _parse(raw: str) -> dict:
    """Tolerant parse of a JSON object from model text (fences / stray prose)."""
    text = (raw or "").strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _split_top(
    stories: list[Story], covered_ids: set[str], *, top_k: int
) -> tuple[list[Story], list[Story], float]:
    ranked = sorted(stories, key=lambda s: s.final_rank, reverse=True)[:top_k]
    covered = [s for s in ranked if s.id in covered_ids]
    uncovered = [s for s in ranked if s.id not in covered_ids]
    coverage = (len(covered) / len(ranked)) if ranked else 1.0
    return ranked, uncovered, round(coverage, 3)


async def recall_check(
    stories: list[Story],
    covered_ids: list[str],
    *,
    llm: LLMClient | None = None,
    top_k: int = DEFAULT_TOP_K,
    quiet_expected: bool = False,
) -> dict:
    """Audit a digest for omissions among the day's top-K ranked stories."""
    client = llm or get_llm()
    ranked, uncovered, coverage = _split_top(stories, set(covered_ids), top_k=top_k)

    missed: list[dict] = []
    if uncovered and not quiet_expected:
        listing = "\n".join(
            f"- {s.title} (rank={s.final_rank:.2f}, family={s.family.value}, "
            f"mentions={s.mention_count})"
            for s in uncovered
        )
        prompt = (
            "You audit a personal AI-news digest for OMISSIONS. The digest covered "
            "the day's top stories EXCEPT those listed below. For each that was "
            "important enough that dropping it genuinely hurts a reader who tracks "
            "LLMs, RL for NLP, efficient/scalable NLP, and multi-agent research, add "
            "it to `missed` with a one-line why. If none were important, return an "
            "empty list.\n\n"
            f"UNCOVERED STORIES:\n{listing}\n"
        )
        raw = await client.generate(prompt, json_schema=_MISSED_SCHEMA, temperature=0.0)
        parsed = _parse(raw)
        missed = [
            m
            for m in (parsed.get("missed") or [])
            if isinstance(m, dict) and str(m.get("title", "")).strip()
        ]

    return {
        "coverage": coverage,
        "top_k": len(ranked),
        "uncovered_titles": [s.title for s in uncovered],
        "missed": missed,
        "missed_count": len(missed),
    }


def recall_gate(
    result: dict, *, quiet_expected: bool, coverage_floor: float = DEFAULT_COVERAGE_FLOOR
) -> list[str]:
    """Return failure messages ([] = pass). Lenient on quiet days."""
    if quiet_expected:
        return []
    fails: list[str] = []
    if float(result["coverage"]) < coverage_floor:
        fails.append(f"top-{result['top_k']} coverage {result['coverage']} < floor {coverage_floor}")
    if int(result["missed_count"]) > 0:
        titles = ", ".join(str(m.get("title", "?")) for m in result["missed"])
        fails.append(f"{result['missed_count']} important story(ies) dropped: {titles}")
    return fails


__all__ = ["recall_check", "recall_gate", "DEFAULT_TOP_K", "DEFAULT_COVERAGE_FLOOR"]
