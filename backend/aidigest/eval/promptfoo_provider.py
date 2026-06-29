"""promptfoo Python provider — A/B the editorial JUDGE prompt vs the golden set.

promptfoo (https://promptfoo.dev) is a Node CLI; this is the provider it calls
(``python:promptfoo_provider.py``). Each test case supplies a rendered ``digest``
plus ``quiet_expected``; we grade it through ``eval.judge.grade_digest`` in the
CURRENT code/prompt state and return the verdict so promptfoo can assert the
flexibility principle and diff two prompt versions before they ship.

Run (from ``backend/``):
    npx promptfoo@latest eval -c aidigest/eval/promptfoo.yaml
Set ``AIDIGEST_LLM_MOCK=0`` + ``GEMINI_API_KEY`` for real grading; the mock
floors numeric scores but still exercises the honesty gate deterministically.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


def call_api(prompt: str, options: Any = None, context: Any = None) -> dict:
    """promptfoo entrypoint. Returns ``{"output": <json string>}``."""
    variables = (context or {}).get("vars", {}) if isinstance(context, dict) else {}
    digest = str(variables.get("digest") or prompt or "")
    quiet_expected = bool(variables.get("quiet_expected", False))
    verdict = asyncio.run(_grade(digest, quiet_expected))
    return {"output": json.dumps(verdict)}


async def _grade(digest_markdown: str, quiet_expected: bool) -> dict:
    from aidigest.eval.judge import grade_digest
    from aidigest.llm.factory import get_llm

    result = await grade_digest(
        digest_markdown, quiet_expected=quiet_expected, llm=get_llm()
    )
    return {
        "total": result["total"],
        "quiet_ok": result["quiet_ok"],
        "scores": result["scores"],
        "notes": result["notes"],
    }


__all__ = ["call_api"]
