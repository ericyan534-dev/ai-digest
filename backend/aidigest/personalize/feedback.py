"""Loops 2 & 3 — feedback learning and natural-language steering.

Loop 2 (``recompute_interest_vector``): the interest vector drifts toward what
the reader 👍'd and away from what they 👎'd. We compute a time-decayed weighted
centroid of liked-story embeddings minus disliked-story embeddings and blend it
with the static profile vector from Loop 1.

Loop 3 (``apply_nl_instruction``): a free-text "tune my feed" instruction is
turned into a NEW profile dict (adjusted ranking weights / added mutes) via the
LLM in JSON mode. Nothing is written to disk here — the caller decides.

``feedback_boosts`` maps a target id to a per-story ranking delta from the recent
up/down/click/dwell signals (time-decayed), consumed by ``process/rank.py``.

All embeddings stay L2-normalized at length ``embed_dim``. All LLM access flows
through ``aidigest.llm.factory.get_llm()``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm
from aidigest.models import Feedback, FeedbackSignal
from aidigest.personalize.profile import build_interest_vector

if TYPE_CHECKING:  # avoid a hard import of the db module (separate implementer)
    from aidigest.db.repo import Repo

# How much each signal nudges the centroid / boost (sign + magnitude).
_SIGNAL_WEIGHT: dict[FeedbackSignal, float] = {
    FeedbackSignal.UP: 1.0,
    FeedbackSignal.DOWN: -1.0,
    FeedbackSignal.CLICK: 0.25,
    FeedbackSignal.DWELL: 0.15,  # scaled by dwell seconds below
}

# Weight given to fresh feedback when blending against the static profile vector.
_FEEDBACK_BLEND = 0.5


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _decay(created_at: datetime, *, half_life_days: float, now: datetime) -> float:
    """Exponential time-decay in [0, 1]: 0.5 at one half-life of age."""
    if half_life_days <= 0:
        return 1.0
    created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - created).total_seconds() / 86_400.0)
    return float(0.5 ** (age_days / half_life_days))


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return list(vec)
    return [v / norm for v in vec]


def feedback_boosts(
    feedback: list[Feedback], *, half_life_days: float = 14.0
) -> dict[str, float]:
    """Map ``target_id -> ranking delta`` from up/down/click/dwell signals.

    Each event contributes ``signal_weight * value_factor * time_decay``. Deltas
    accumulate per target. NL-instruction events are ignored here (they reshape
    the whole profile, not one story).
    """
    now = _utcnow()
    boosts: dict[str, float] = {}
    for fb in feedback:
        base = _SIGNAL_WEIGHT.get(fb.signal)
        if base is None:
            continue
        # For dwell, scale by seconds (capped) so long reads count more.
        if fb.signal == FeedbackSignal.DWELL:
            value_factor = min(float(fb.value), 120.0) / 120.0
        else:
            value_factor = 1.0
        delta = base * value_factor * _decay(
            fb.created_at, half_life_days=half_life_days, now=now
        )
        boosts[fb.target_id] = boosts.get(fb.target_id, 0.0) + delta
    return boosts


async def recompute_interest_vector(
    repo: Repo,
    profile: dict,
    *,
    llm: LLMClient | None = None,
    half_life_days: float = 14.0,
) -> list[float]:
    """Loop 2: decayed centroid of 👍'd MINUS 👎'd story embeddings, blended with
    the static profile vector. Returns an L2-normalized vector of ``embed_dim``.

    Stories without an embedding are skipped. When there is no usable feedback,
    this returns the static profile vector unchanged.
    """
    client = llm or get_llm()
    base_vector = await build_interest_vector(profile, llm=client)

    feedback = await repo.get_feedback()
    now = _utcnow()

    # Accumulate signed, time-decayed weights per story target.
    weights: dict[str, float] = {}
    for fb in feedback:
        if fb.signal not in (FeedbackSignal.UP, FeedbackSignal.DOWN):
            continue
        sign = 1.0 if fb.signal == FeedbackSignal.UP else -1.0
        decay = _decay(fb.created_at, half_life_days=half_life_days, now=now)
        weights[fb.target_id] = weights.get(fb.target_id, 0.0) + sign * decay

    if not weights:
        return base_vector

    stories = await repo.get_stories_by_ids(list(weights.keys()))
    dim = len(base_vector)
    centroid = [0.0] * dim
    total_weight = 0.0
    for story in stories:
        emb = story.embedding
        if not emb or len(emb) != dim:
            continue
        w = weights.get(story.id, 0.0)
        if w == 0.0:
            continue
        for i, v in enumerate(emb):
            centroid[i] += w * v
        total_weight += abs(w)

    if total_weight == 0.0:
        return base_vector

    centroid = [c / total_weight for c in centroid]
    centroid = _l2_normalize(centroid)

    # Blend static profile vector with the feedback centroid, then renormalize.
    blended = [
        (1.0 - _FEEDBACK_BLEND) * base_vector[i] + _FEEDBACK_BLEND * centroid[i]
        for i in range(dim)
    ]
    return _l2_normalize(blended)


# JSON schema the LLM must fill when interpreting a steering instruction.
_NL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "alpha": {"type": "number"},  # importance weight
        "beta": {"type": "number"},  # personal-fit weight
        "gamma": {"type": "number"},  # diversity weight
        "add_mutes": {"type": "array", "items": {"type": "string"}},
        "add_subfields": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
}


def _clamp01(value: object, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


async def apply_nl_instruction(
    instruction: str, profile: dict, *, llm: LLMClient | None = None
) -> dict:
    """Loop 3: turn a NL steering instruction into a NEW profile dict.

    The LLM proposes adjusted ranking weights, added mutes, and added subfields;
    we apply them defensively (clamp weights to [0,1], dedupe lists) onto a COPY
    of the profile. Does NOT write to disk.
    """
    if not instruction or not instruction.strip():
        return dict(profile)

    client = llm or get_llm()
    ranking = dict(profile.get("ranking") or {})
    prompt = (
        "You tune a personal AI-news feed. The reader gave this instruction:\n"
        f'  "{instruction.strip()}"\n\n'
        "Current ranking weights (each 0..1): "
        f"alpha={ranking.get('alpha', 0.5)} (importance), "
        f"beta={ranking.get('beta', 0.4)} (personal fit), "
        f"gamma={ranking.get('gamma', 0.1)} (diversity).\n"
        f"Current mutes: {profile.get('mutes', [])}.\n"
        f"Current subfields: {profile.get('subfields', [])}.\n\n"
        "Return JSON adjusting alpha/beta/gamma to reflect the instruction, plus "
        "any topics to add to mutes (add_mutes) or subfields to add "
        "(add_subfields). Keep weights in [0,1]. Include a short rationale."
    )

    raw = await client.generate(prompt, json_schema=_NL_SCHEMA, temperature=0.2)
    parsed = _parse_json(raw)

    new_profile = dict(profile)

    # --- ranking weights ---
    new_ranking = dict(ranking)
    new_ranking["alpha"] = _clamp01(parsed.get("alpha"), float(ranking.get("alpha", 0.5)))
    new_ranking["beta"] = _clamp01(parsed.get("beta"), float(ranking.get("beta", 0.4)))
    new_ranking["gamma"] = _clamp01(parsed.get("gamma"), float(ranking.get("gamma", 0.1)))
    new_profile["ranking"] = new_ranking

    # --- mutes ---
    add_mutes = [str(m).strip() for m in parsed.get("add_mutes", []) if str(m).strip()]
    if add_mutes:
        existing = list(profile.get("mutes") or [])
        new_profile["mutes"] = _dedupe(existing + add_mutes)

    # --- subfields ---
    add_subfields = [
        str(s).strip() for s in parsed.get("add_subfields", []) if str(s).strip()
    ]
    if add_subfields:
        existing_sf = list(profile.get("subfields") or [])
        new_profile["subfields"] = _dedupe(existing_sf + add_subfields)

    return new_profile


def _dedupe(values: list[str]) -> list[str]:
    """Order-preserving case-insensitive dedupe."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _parse_json(raw: str) -> dict:
    """Parse a JSON object from model output; tolerate fences / stray text."""
    import json

    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return {}
        else:
            return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "recompute_interest_vector",
    "feedback_boosts",
    "apply_nl_instruction",
]
