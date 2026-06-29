"""Prompt template loader for the generation stage.

Templates live next to this module as plain ``.md`` files. They are loaded once
and cached. The VOICE system prompt is reused across every generation call so
the editorial voice (Karpathy + smol.ai + LeCun + Ng) stays consistent.

The flexibility principle is encoded directly in ``voice.md`` and reinforced by
per-tier instructions injected at render time (see ``tier_instruction``).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from aidigest.models import ImportanceTier

_PROMPT_DIR = Path(__file__).resolve().parent

# Names of the shipped templates (kept explicit so a missing file fails loudly).
VOICE = "voice"
DAILY_MAP = "daily_map"
DAILY_REDUCE = "daily_reduce"
WEEKLY_CANDIDATE = "weekly_candidate"
WEEKLY_POLISH = "weekly_polish"
EXEMPLARS = "exemplars"


@cache
def load_prompt(name: str) -> str:
    """Load a prompt template by base name (without extension), cached."""
    path = _PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def voice_prompt() -> str:
    """The reusable system prompt (voice + flexibility principle)."""
    return load_prompt(VOICE)


# Per-tier writing instruction injected into the map prompt. This is the
# flexibility principle made concrete at the call site (depth scales with tier).
_TIER_INSTRUCTIONS: dict[ImportanceTier, str] = {
    ImportanceTier.BREAKTHROUGH: (
        "BREAKTHROUGH — go FULL DEPTH. Cover what shipped, the mechanism, the "
        "background/context the reader needs, why it is not just an increment, "
        "and the open question it raises. Several sentences. You may not bury it."
    ),
    ImportanceTier.NOTABLE: (
        "NOTABLE — 2 to 4 sentences. State the result, the delta vs prior work, "
        "and the single most relevant reason it matters to this reader."
    ),
    ImportanceTier.MINOR: (
        "MINOR — exactly ONE line. Name it, say what it is, stop. Do not inflate "
        "it into a headline or a trend."
    ),
    ImportanceTier.QUIET_DAY: (
        "QUIET_DAY — nothing major. Say so honestly ('Quiet day — nothing major "
        "shipped'). Do not manufacture importance. If there is no important paper, "
        "say there is no important paper."
    ),
}


def tier_instruction(tier: ImportanceTier) -> str:
    """Return the writing instruction for the active importance tier."""
    return _TIER_INSTRUCTIONS[tier]


__all__ = [
    "load_prompt",
    "voice_prompt",
    "tier_instruction",
    "VOICE",
    "DAILY_MAP",
    "DAILY_REDUCE",
    "WEEKLY_CANDIDATE",
    "WEEKLY_POLISH",
    "EXEMPLARS",
]
