"""The flexibility-principle gate (CRITICAL).

This module decides how much the generator is allowed to write about each story
by classifying it into an :class:`ImportanceTier`. The daily/weekly generators
read the resulting tier and scale depth accordingly:

    BREAKTHROUGH -> full depth + background
    NOTABLE      -> 2-4 sentence takeaway + why-it-matters
    MINOR        -> one line
    QUIET_DAY    -> "Quiet day — nothing major shipped" (honest, no padding)

Thresholds live in ``profile.yaml`` under ``tiers``. We classify a story by its
``final_rank`` (the blended ranking score from ``process/rank.py``), interpreted
relative to the absolute profile thresholds. ``day_top_score`` is passed so the
day as a whole can be judged quiet even when individual stories scrape the bar.

Pure functions only — no I/O, no LLM. Deterministic and unit-testable at every
boundary (acceptance gate item (d)).
"""

from __future__ import annotations

from aidigest.models import ImportanceTier, Story

# Fallback thresholds used when profile.yaml omits a key. Mirrors profile.yaml.
_DEFAULT_TIERS: dict[str, float] = {
    "breakthrough_min_score": 0.72,
    "breakthrough_importance_override": 0.55,
    "notable_min_score": 0.40,
    "minor_min_score": 0.30,
    "quiet_day_top_score": 0.40,  # legacy final_rank gate (standalone classify_tier)
    "quiet_day_min_importance": 0.32,  # OBJECTIVE quiet gate: max story importance
    "notable_day_min_importance": 0.32,  # day is NOTABLE (not quiet) at/above this
}


def _tiers(profile: dict) -> dict[str, float]:
    """Read tier thresholds from the profile, falling back to defaults."""
    raw = (profile or {}).get("tiers") or {}
    merged = dict(_DEFAULT_TIERS)
    for key, default in _DEFAULT_TIERS.items():
        value = raw.get(key, default)
        try:
            merged[key] = float(value)
        except (TypeError, ValueError):
            merged[key] = default
    return merged


def classify_tier(
    story: Story,
    *,
    profile: dict,
    day_top_score: float,
    quiet_day: bool | None = None,
) -> ImportanceTier:
    """Classify one story into an :class:`ImportanceTier`.

    ``quiet_day`` is the DAY-level decision (made in ``classify_day`` from the
    day's objective max importance). When True the whole day is quiet, so even
    the top story is only ever MINOR/QUIET_DAY. When None it is derived from
    ``day_top_score`` (legacy behavior for standalone callers/tests). Within a
    non-quiet day, breakthrough/notable/minor bands use the blended ``final_rank``
    (personal-relevant ordering), plus the importance override below.
    """
    t = _tiers(profile)
    score = float(story.final_rank)

    if quiet_day is None:
        quiet_day = day_top_score < t["quiet_day_top_score"]

    # Whole-day quiet gate: nothing is a breakthrough/notable item on a quiet day.
    if quiet_day:
        # The single best story may still merit a one-line MINOR note; everything
        # at/under the minor floor is QUIET_DAY.
        if score >= t["minor_min_score"] and score >= day_top_score:
            return ImportanceTier.MINOR
        return ImportanceTier.QUIET_DAY

    # Absolute-importance override (the "cannot miss it" guarantee): a story with
    # very high RAW importance — many independent cross-source mentions + high
    # authority — is a BREAKTHROUGH even if the personal/embedding term is weak or
    # absent (cold start, mock mode, missing embeddings). This decouples the core
    # guarantee from the personalization signal so a revolutionary story is never
    # silently downgraded to NOTABLE.
    if float(story.importance) >= t["breakthrough_importance_override"]:
        return ImportanceTier.BREAKTHROUGH

    if score >= t["breakthrough_min_score"]:
        return ImportanceTier.BREAKTHROUGH
    if score >= t["notable_min_score"]:
        return ImportanceTier.NOTABLE
    if score >= t["minor_min_score"]:
        return ImportanceTier.MINOR
    return ImportanceTier.QUIET_DAY


# Tier ordering for computing the "max tier present" across a day.
_TIER_RANK: dict[ImportanceTier, int] = {
    ImportanceTier.QUIET_DAY: 0,
    ImportanceTier.MINOR: 1,
    ImportanceTier.NOTABLE: 2,
    ImportanceTier.BREAKTHROUGH: 3,
}


def classify_day(
    stories: list[Story], *, profile: dict
) -> tuple[list[Story], ImportanceTier, bool]:
    """Classify every story and judge the day as a whole.

    Returns ``(stories_with_tier, overall_tier, quiet_day)`` where:

    * ``stories_with_tier`` are NEW Story copies with ``.tier`` set (immutable).
    * ``overall_tier`` is the maximum tier present (QUIET_DAY when empty/quiet).
    * ``quiet_day`` is True when the day's max OBJECTIVE importance is below
      ``tiers.quiet_day_min_importance`` — the honest "nothing major shipped"
      gate. It is based on importance (cross-source mentions + engagement +
      citation), NOT final_rank, so a routine-but-on-topic item can't mask a
      quiet day via its personal-fit score.
    """
    t = _tiers(profile)
    if not stories:
        return [], ImportanceTier.QUIET_DAY, True

    day_top_importance = max(float(s.importance) for s in stories)
    day_top_score = max(float(s.final_rank) for s in stories)
    quiet_day = day_top_importance < t["quiet_day_min_importance"]

    tagged: list[Story] = []
    overall = ImportanceTier.QUIET_DAY
    for story in stories:
        tier = classify_tier(
            story, profile=profile, day_top_score=day_top_score, quiet_day=quiet_day
        )
        tagged.append(story.model_copy(update={"tier": tier}))
        if _TIER_RANK[tier] > _TIER_RANK[overall]:
            overall = tier

    # When the day is quiet, the overall tier is QUIET_DAY even if a single
    # MINOR note survives — the digest should read as a quiet day.
    if quiet_day:
        overall = ImportanceTier.QUIET_DAY

    return tagged, overall, quiet_day


def classify_day_tier(stories: list[Story], *, profile: dict) -> ImportanceTier:
    """The day's overall tier — a 3-LEVEL signal that drives digest depth:

    * BREAKTHROUGH — a genuinely major release/result (full-depth lead coverage).
    * NOTABLE      — meaningful news, but nothing breakthrough (lead the top items,
                     summarize the rest as trends).
    * QUIET_DAY    — nothing notable shipped (honest "quiet day" + trend recaps only).

    Driven by objective importance (cross-source + engagement + citation), NOT
    personal fit, so a quiet day can't be masked by on-topic-but-routine items.
    """
    t = _tiers(profile)
    if not stories:
        return ImportanceTier.QUIET_DAY
    top = max(float(s.importance) for s in stories)
    if top >= t["breakthrough_importance_override"]:
        return ImportanceTier.BREAKTHROUGH
    if top >= t["notable_day_min_importance"]:
        return ImportanceTier.NOTABLE
    return ImportanceTier.QUIET_DAY


def tier_for(story: Story, *, profile: dict | None = None, day_top_score: float | None = None) -> ImportanceTier:
    """Convenience: the tier for a single story.

    If ``day_top_score`` is omitted, the story's own ``final_rank`` is used as the
    day top (i.e. judge the story in isolation). If ``profile`` is omitted, the
    default thresholds are used.
    """
    prof = profile or {}
    top = float(story.final_rank) if day_top_score is None else day_top_score
    return classify_tier(story, profile=prof, day_top_score=top)


def is_quiet_day(stories: list[Story], *, profile: dict | None = None) -> bool:
    """Convenience: True when the day's max objective importance is below the gate."""
    t = _tiers(profile or {})
    if not stories:
        return True
    day_top_importance = max(float(s.importance) for s in stories)
    return day_top_importance < t["quiet_day_min_importance"]


__all__ = [
    "classify_tier",
    "classify_day",
    "tier_for",
    "is_quiet_day",
]
