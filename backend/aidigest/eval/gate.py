"""Golden-set eval gate — a regression gate for generator/prompt changes.

Runs a daily digest over a known story set and checks the flexibility principle
holds end-to-end:

  * quiet set  -> the digest is marked quiet AND passes the honesty gate
                 (no manufactured importance).
  * busy set   -> the digest is NOT quiet, a BREAKTHROUGH surfaces, and (live
                 only) the LLM-judge editorial total clears a floor.

`evaluate_daily` is mode-agnostic (mock for CI determinism, live for real
grading). `gate_failures` returns a list of human-readable failures ([] = pass)
so both the pytest gate and the `make eval` script share one source of truth.
"""

from __future__ import annotations

from aidigest.deliver.render_md import render_daily_md
from aidigest.eval.judge import grade_digest
from aidigest.generate.daily import generate_daily
from aidigest.generate.importance import classify_day
from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm
from aidigest.models import ImportanceTier, Item, Story


async def evaluate_daily(
    stories: list[Story],
    items_by_id: dict[str, Item],
    *,
    profile: dict,
    quiet_expected: bool,
    date: str = "2026-01-01",
    llm: LLMClient | None = None,
) -> dict:
    """Classify -> generate -> render -> grade one daily digest; return a verdict dict."""
    client = llm or get_llm()
    tagged, _overall, _quiet = classify_day(stories, profile=profile)
    digest = await generate_daily(
        tagged, items_by_id, profile=profile, date=date, llm=client
    )
    markdown = render_daily_md(digest)
    grade = await grade_digest(markdown, quiet_expected=quiet_expected, llm=client)
    has_breakthrough = (
        digest.overall_tier == ImportanceTier.BREAKTHROUGH
        or any(s.tier == ImportanceTier.BREAKTHROUGH for s in tagged)
    )
    return {
        "quiet_day": digest.quiet_day,
        "overall_tier": digest.overall_tier.value,
        "has_breakthrough": has_breakthrough,
        "total": grade["total"],
        "quiet_ok": grade["quiet_ok"],
        "scores": grade["scores"],
        "notes": grade["notes"],
        "markdown": markdown,
    }


def gate_failures(
    result: dict, *, quiet_expected: bool, min_total: float | None = None
) -> list[str]:
    """Return failure messages for one evaluated set ([] means it passed).

    `min_total` (live only) enforces a minimum LLM-judge editorial total; pass
    None in mock mode, where the deterministic judge floors all scores.
    """
    fails: list[str] = []
    if quiet_expected:
        if result["quiet_day"] is not True:
            fails.append("quiet day expected but the digest is not marked quiet")
        if result["quiet_ok"] is not True:
            fails.append(f"quiet-day honesty gate failed ({result['notes']})")
    else:
        if result["quiet_day"] is True:
            fails.append("busy day expected but the digest is marked quiet")
        if not result["has_breakthrough"]:
            fails.append("busy day expected a BREAKTHROUGH but none surfaced")
        if min_total is not None and float(result["total"]) < min_total:
            fails.append(
                f"editorial total {result['total']} < floor {min_total}"
            )
    return fails


__all__ = ["evaluate_daily", "gate_failures"]
