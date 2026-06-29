"""Loop 1 — static interests: load ``profile.yaml`` -> interest vector.

The profile is the personalization seed. We embed its subfields, voices, and
venues into ONE L2-normalized interest vector that drives the ``personal``
ranking score and the "why it matters to you" angle in every digest.

All LLM access goes through ``aidigest.llm.factory.get_llm()`` so the mock and
real Gemini client are interchangeable. The interest vector is embedded with
``task_type='RETRIEVAL_QUERY'`` (it is the query side of retrieval).
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm

# Default profile location: backend/profile.yaml (two levels up from this file:
# .../backend/aidigest/personalize/profile.py -> parents[2] == .../backend).
_DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "profile.yaml"

# Keys the profile MUST contain for the pipeline to function.
_REQUIRED_KEYS: tuple[str, ...] = ("subfields", "voice", "ranking")


def load_profile(path: str | None = None) -> dict:
    """Load ``profile.yaml`` (default: ``backend/profile.yaml``) into a dict.

    Validates that required keys are present and fails fast with a clear error
    when the profile is malformed (input validation at a system boundary).
    """
    profile_path = Path(path) if path else _DEFAULT_PROFILE_PATH
    if not profile_path.is_file():
        raise FileNotFoundError(f"profile not found: {profile_path}")

    try:
        loaded = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ValueError(f"profile is not valid YAML: {profile_path}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValueError(f"profile must be a mapping, got {type(loaded).__name__}")

    missing = [k for k in _REQUIRED_KEYS if k not in loaded]
    if missing:
        raise ValueError(f"profile missing required keys: {', '.join(missing)}")

    return loaded


def _as_list(value: object) -> list[str]:
    """Coerce a profile field into a clean list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def profile_text(profile: dict) -> str:
    """Build the text blob embedded to form the interest vector.

    Concatenates the reader's subfields, the venues they follow, and the voices
    they emulate into a single descriptive query. This is the canonical text so
    the embedding is reproducible.
    """
    subfields = _as_list(profile.get("subfields"))
    venues = _as_list(profile.get("venues"))

    voice = profile.get("voice") or {}
    voices = _as_list(voice.get("emulate")) if isinstance(voice, dict) else []
    if not voices:
        voices = _as_list(profile.get("must_follow"))

    lines: list[str] = []
    if subfields:
        lines.append("Research subfields: " + "; ".join(subfields) + ".")
    if venues:
        lines.append("Primary venues: " + ", ".join(venues) + ".")
    if voices:
        lines.append("Voices followed: " + ", ".join(voices) + ".")
    if not lines:
        # Defensive: never embed an empty string.
        lines.append("Artificial intelligence research and engineering.")
    return "\n".join(lines)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return list(vec)
    return [v / norm for v in vec]


async def build_interest_vector(
    profile: dict, *, llm: LLMClient | None = None
) -> list[float]:
    """Embed the profile into ONE L2-normalized interest vector.

    Uses ``task_type='RETRIEVAL_QUERY'``. The returned vector has length
    ``embed_dim`` (1536) and unit L2 norm.
    """
    client = llm or get_llm()
    text = profile_text(profile)
    vectors = await client.embed([text], task_type="RETRIEVAL_QUERY")
    if not vectors:  # pragma: no cover - defensive
        raise RuntimeError("embedder returned no vectors for the interest profile")
    return _l2_normalize(vectors[0])


__all__ = [
    "load_profile",
    "profile_text",
    "build_interest_vector",
]
