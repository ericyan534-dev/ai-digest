"""'Did-I-miss-anything' recall eval over the latest daily digest (`make recall`).

Loads the most recent daily digest, compares the stories it covered against the
day's full ranked story set, and reports omissions (deterministic coverage +
an LLM omission pass). Records the result in ``eval_runs`` and exits non-zero
when the recall gate fails so it can block a deploy. Needs a reachable DB; uses
the active LLM (set AIDIGEST_LLM_MOCK=0 + GEMINI_API_KEY for a real omission pass).
"""

from __future__ import annotations

import asyncio
import sys

from aidigest.db.repo import get_repo
from aidigest.eval.recall import recall_check, recall_gate
from aidigest.llm.factory import get_llm
from aidigest.models import DailyDigest
from scripts._common import setup_logging


async def _main() -> None:
    repo = await get_repo()
    rows = await repo.list_digests(kind=None, limit=30)
    daily_row = next((r for r in rows if r.get("kind") == "daily"), None)
    if daily_row is None:
        print("no daily digest found — run `make daily` first", file=sys.stderr)
        raise SystemExit(2)

    digest = await repo.get_digest(daily_row["id"])
    if not isinstance(digest, DailyDigest):
        print("latest digest is not a daily", file=sys.stderr)
        raise SystemExit(2)

    stories = await repo.get_stories_for_date(digest.date)
    result = await recall_check(
        stories,
        list(digest.story_ids),
        llm=get_llm(),
        quiet_expected=digest.quiet_day,
    )
    fails = recall_gate(result, quiet_expected=digest.quiet_day)

    await repo.save_eval_run(
        digest_id=digest.id,
        judge_model=getattr(get_llm(), "model", ""),
        scores={"coverage": result["coverage"], "missed_count": result["missed_count"]},
        notes="; ".join(fails) if fails else "recall ok",
    )

    status = "PASS" if not fails else "FAIL"
    print(
        f"[{status}] recall for {digest.id}: coverage={result['coverage']} "
        f"top_k={result['top_k']} missed={result['missed_count']} "
        f"quiet={digest.quiet_day}"
    )
    for title in (m.get("title", "?") for m in result["missed"]):
        print(f"        - dropped: {title}")
    if fails:
        print(f"\nRECALL GATE FAILED ({len(fails)} issue(s))", file=sys.stderr)
        raise SystemExit(1)
    print("\nRECALL GATE PASSED")


def main() -> None:
    setup_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
