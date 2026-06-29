"""promptfoo provider — grades a digest through the editorial judge honesty gate."""

from __future__ import annotations

import json
import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

from aidigest.eval.promptfoo_provider import call_api  # noqa: E402


def test_call_api_quiet_day_is_honest() -> None:
    out = call_api(
        "",
        None,
        {"vars": {"quiet_expected": True, "digest": "Quiet day — nothing major shipped."}},
    )
    data = json.loads(out["output"])
    assert data["quiet_ok"] is True
    assert "total" in data and "scores" in data


def test_call_api_inflated_quiet_day_is_caught() -> None:
    out = call_api(
        "",
        None,
        {
            "vars": {
                "quiet_expected": True,
                "digest": "A revolutionary, groundbreaking paradigm shift across the board!",
            }
        },
    )
    data = json.loads(out["output"])
    assert data["quiet_ok"] is False
