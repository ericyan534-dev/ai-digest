"""LIVE golden-set eval gate (`make eval`).

Runs the full pipeline (embed -> cluster -> rank -> classify -> generate -> grade)
over the golden busy/quiet sets with the REAL Gemini client, enforcing the
flexibility principle AND an editorial-quality floor (LLM-judge). Exits non-zero
on any failure so it can gate a deploy. Requires AIDIGEST_LLM_MOCK=0 + a key.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from aidigest.config import get_settings
from aidigest.eval.gate import evaluate_daily, gate_failures
from aidigest.eval.golden import golden_items, load_golden
from aidigest.llm.factory import get_llm
from aidigest.personalize.profile import build_interest_vector, load_profile
from aidigest.process.cluster import cluster_into_stories
from aidigest.process.embed import embed_items
from aidigest.process.rank import score_stories
from scripts._common import setup_logging

GOLDEN_SETS = ("busy_day", "quiet_day")
MIN_TOTAL = 3.0  # editorial floor on the 1..5 rubric (live judge only)


async def _evaluate_set(name: str, *, min_total: float) -> tuple[dict, list[str]]:
    meta = load_golden(name)
    profile = load_profile()
    llm = get_llm()
    # Treat the golden fixture as TODAY's news (re-stamp the authoring dates) so
    # recency decay doesn't drag importance as the fixed fixture dates age.
    now = datetime.now(UTC)
    fresh = [it.model_copy(update={"published_at": now}) for it in golden_items(name)]
    items = await embed_items(fresh, llm=llm)
    threshold = float((profile.get("processing") or {}).get("cluster_threshold", 0.86))
    stories = cluster_into_stories(items, threshold=threshold)
    interest = await build_interest_vector(profile, llm=llm)
    stories = score_stories(stories, interest_vector=interest, profile=profile)
    items_by_id = {it.id: it for it in items}
    result = await evaluate_daily(
        stories,
        items_by_id,
        profile=profile,
        quiet_expected=bool(meta["quiet_expected"]),
        date=str(meta.get("date", "2026-01-01")),
        llm=llm,
    )
    fails = gate_failures(
        result, quiet_expected=bool(meta["quiet_expected"]), min_total=min_total
    )
    return result, fails


async def _main(min_total: float) -> None:
    if get_settings().llm_mock:
        print(
            "ERROR: eval_gate is a LIVE gate. Set AIDIGEST_LLM_MOCK=0 + GEMINI_API_KEY "
            "(the mock judge floors scores). For the deterministic gate run `make test`.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    all_fail: list[str] = []
    for name in GOLDEN_SETS:
        result, fails = await _evaluate_set(name, min_total=min_total)
        status = "PASS" if not fails else "FAIL"
        print(
            f"[{status}] {name}: tier={result['overall_tier']} quiet={result['quiet_day']} "
            f"breakthrough={result['has_breakthrough']} total={result['total']} "
            f"quiet_ok={result['quiet_ok']}"
        )
        for f in fails:
            print(f"        - {f}")
        all_fail += fails

    if all_fail:
        print(f"\nEVAL GATE FAILED ({len(all_fail)} issue(s))", file=sys.stderr)
        raise SystemExit(1)
    print("\nEVAL GATE PASSED")


def main() -> None:
    ap = argparse.ArgumentParser(description="LIVE golden-set eval gate.")
    ap.add_argument("--min-total", type=float, default=MIN_TOTAL,
                    help=f"editorial total floor on the 1..5 scale (default {MIN_TOTAL})")
    args = ap.parse_args()
    setup_logging()
    asyncio.run(_main(args.min_total))


if __name__ == "__main__":
    main()
