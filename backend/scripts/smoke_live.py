"""LIVE smoke test: force the REAL Gemini client over a tiny fixed item set.

This is gate item (g): with a real GEMINI_API_KEY and AIDIGEST_LLM_MOCK=0,
generate a coherent daily digest end-to-end through the LLM path (embed ->
cluster -> rank -> classify -> generate_daily), assert it is a valid
`DailyDigest`, and print it.

NO database required — it runs over a fixed in-memory item set so the smoke
isolates the LLM/network path (thought tokens, MAX_TOKENS, TLS-reset retries).
Run via `make smoke` (which sets AIDIGEST_LLM_MOCK=0) or directly:

    AIDIGEST_LLM_MOCK=0 python -m scripts.smoke_live
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from aidigest.config import get_settings
from aidigest.deliver.render_md import render_daily_md
from aidigest.generate.daily import generate_daily
from aidigest.generate.importance import classify_day
from aidigest.llm.factory import get_llm
from aidigest.models import DailyDigest, Family, Item
from aidigest.personalize.profile import build_interest_vector, load_profile
from aidigest.process.cluster import cluster_into_stories
from aidigest.process.embed import embed_items
from aidigest.process.rank import score_stories
from scripts._common import setup_logging


def _fixed_items() -> list[Item]:
    """A tiny, deterministic, breakthrough-flavored item set for the smoke."""
    now = datetime.now(UTC)
    return [
        Item.create(
            source="arxiv",
            family=Family.ACADEMIA,
            title="A linear-attention transformer matching softmax at 1M context",
            url="https://arxiv.org/abs/2606.00001",
            raw_text=(
                "We introduce a linear-attention variant that matches full softmax "
                "attention on long-context reasoning while cutting memory 8x. Strong "
                "results on NeurIPS-scale benchmarks for efficient and scalable NLP."
            ),
            published_at=now,
            metrics={"citations": 12},
        ),
        Item.create(
            source="hn",
            family=Family.COMMUNITY,
            title="DeepSeek V4 ships a new RL post-training recipe",
            url="https://news.ycombinator.com/item?id=99999999",
            raw_text=(
                "DeepSeek V4 open-weights release. New RL-for-NLP post-training recipe, "
                "multi-agent self-play, big jump on agentic benchmarks."
            ),
            published_at=now,
            metrics={"upvotes": 1200, "comments": 400},
        ),
        Item.create(
            source="rss:industry",
            family=Family.INDUSTRY,
            title="A vendor bumps a minor SDK version",
            url="https://example.com/sdk-1-2-3",
            raw_text="Routine SDK patch release. Bug fixes, no new capabilities.",
            published_at=now,
            metrics={},
        ),
    ]


async def _run_live() -> DailyDigest:
    settings = get_settings()
    if settings.llm_mock:
        print(
            "ERROR: smoke_live requires the REAL client. Set AIDIGEST_LLM_MOCK=0 "
            "and provide GEMINI_API_KEY.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is empty; cannot run the live smoke.", file=sys.stderr)
        raise SystemExit(2)

    llm = get_llm()
    profile = load_profile()
    date = datetime.now(UTC).date().isoformat()

    items = await embed_items(_fixed_items(), llm=llm)
    stories = cluster_into_stories(items)
    interest = await build_interest_vector(profile, llm=llm)
    stories = score_stories(stories, interest_vector=interest, profile=profile)
    stories, _tier, _quiet = classify_day(stories, profile=profile)
    items_by_id = {it.id: it for it in items}
    return await generate_daily(stories, items_by_id, profile=profile, date=date, llm=llm)


def _assert_valid(digest: DailyDigest) -> None:
    """Pydantic-validate + sanity-check the live output (fail loudly otherwise)."""
    DailyDigest.model_validate(digest.model_dump(mode="json"))
    assert digest.tldr.strip(), "empty TL;DR"
    assert digest.overall_tier is not None, "missing overall tier"
    assert digest.model, "digest did not record a model id"


async def _main() -> None:
    digest = await _run_live()
    _assert_valid(digest)
    print("=" * 72)
    print(render_daily_md(digest))
    print("=" * 72)
    print(
        f"OK valid DailyDigest id={digest.id} tier={digest.overall_tier.value} "
        f"quiet={digest.quiet_day} sections={len(digest.sections)} model={digest.model}"
    )


def main() -> None:
    argparse.ArgumentParser(description="LIVE Gemini smoke test (tiny fixed ingest).").parse_args()
    setup_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
