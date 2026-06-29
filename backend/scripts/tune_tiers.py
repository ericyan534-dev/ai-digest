"""LIVE tier-threshold tuning harness (`make tune`).

Pulls REAL items from the working adapters, embeds them with the real Gemini
client, clusters -> ranks -> classifies, and reports the actual distributions of
``importance`` and ``final_rank`` plus the resulting tier histogram. Then sweeps
candidate threshold sets so you can see how the flexibility principle holds on
real data before committing thresholds to profile.yaml.

In-memory only (no DB needed). Requires AIDIGEST_LLM_MOCK=0 + GEMINI_API_KEY.

    python -m scripts.tune_tiers --hours 48 --max-items 80
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

from aidigest.config import get_settings
from aidigest.generate.importance import classify_day
from aidigest.ingest.registry import ingest_all
from aidigest.llm.factory import get_llm
from aidigest.personalize.profile import build_interest_vector, load_profile
from aidigest.process.cluster import cluster_into_stories
from aidigest.process.embed import embed_items
from aidigest.process.rank import score_stories
from scripts._common import setup_logging

# Candidate threshold sets to sweep (name -> overrides for profile['tiers']).
CANDIDATES: dict[str, dict[str, float]] = {
    "current": {},
    "stricter_breakthrough": {
        "breakthrough_importance_override": 0.62,
        "breakthrough_min_score": 0.78,
    },
    "tighter_notable": {"notable_min_score": 0.60, "minor_min_score": 0.34},
    "looser_quiet": {"quiet_day_min_importance": 0.34},
}


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def _histogram(profile: dict, stories: list[object]) -> tuple[Counter, bool]:
    tagged, _overall, quiet = classify_day(stories, profile=profile)  # type: ignore[arg-type]
    return Counter(s.tier.value for s in tagged), quiet


def _with_tiers(profile: dict, overrides: dict[str, float]) -> dict:
    merged = dict(profile)
    tiers = dict(profile.get("tiers") or {})
    tiers.update(overrides)
    merged["tiers"] = tiers
    return merged


async def _build_stories(hours: int, max_items: int) -> tuple[list, dict]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    profile = load_profile()
    llm = get_llm()

    items = await ingest_all(since)
    items.sort(key=lambda it: it.published_at, reverse=True)
    items = items[:max_items]
    print(f"ingested+capped items={len(items)} (window={hours}h)")
    if not items:
        return [], profile

    embedded = await embed_items(items, llm=llm)
    threshold = float((profile.get("processing") or {}).get("cluster_threshold", 0.86))
    stories = cluster_into_stories(embedded, threshold=threshold)
    interest = await build_interest_vector(profile, llm=llm)
    stories = score_stories(stories, interest_vector=interest, profile=profile)
    return stories, profile


def _report(stories: list, profile: dict) -> None:
    print(f"\nstories={len(stories)}")
    if not stories:
        return

    imps = [float(s.importance) for s in stories]
    ranks = [float(s.final_rank) for s in stories]
    print("\nimportance  p50/p75/p90/max = "
          f"{_pct(imps,0.5):.3f}/{_pct(imps,0.75):.3f}/{_pct(imps,0.9):.3f}/{max(imps):.3f}")
    print("final_rank  p50/p75/p90/max = "
          f"{_pct(ranks,0.5):.3f}/{_pct(ranks,0.75):.3f}/{_pct(ranks,0.9):.3f}/{max(ranks):.3f}")

    print("\ntop stories (importance | personal | final_rank | mentions | family | title):")
    for s in stories[:20]:
        print(f"  {s.importance:.3f} | {s.personal:.3f} | {s.final_rank:.3f} | "
              f"{s.mention_count:>2} | {s.family.value[:4]:<4} | {s.title[:64]}")

    print("\ntier histogram under each candidate threshold set:")
    for name, overrides in CANDIDATES.items():
        prof = _with_tiers(profile, overrides)
        hist, quiet = _histogram(prof, stories)
        bd = "  ".join(f"{k}={hist.get(k, 0)}" for k in ("breakthrough", "notable", "minor", "quiet_day"))
        extra = f" overrides={overrides}" if overrides else ""
        print(f"  [{name:<22}] quiet_day={quiet!s:<5}  {bd}{extra}")


async def _main(hours: int, max_items: int) -> None:
    if get_settings().llm_mock:
        print("ERROR: tune_tiers is LIVE. Set AIDIGEST_LLM_MOCK=0 + GEMINI_API_KEY.", file=sys.stderr)
        raise SystemExit(2)
    stories, profile = await _build_stories(hours, max_items)
    _report(stories, profile)


def main() -> None:
    ap = argparse.ArgumentParser(description="LIVE tier-threshold tuning harness.")
    ap.add_argument("--hours", type=int, default=48, help="look-back window (hours)")
    ap.add_argument("--max-items", type=int, default=80, help="cap items embedded (cost)")
    args = ap.parse_args()
    setup_logging("WARNING")
    asyncio.run(_main(args.hours, args.max_items))


if __name__ == "__main__":
    main()
