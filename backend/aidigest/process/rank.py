"""Rank stories: importance + personal fit + diversity, blended per profile.

final_rank = alpha*importance + beta*personal + gamma*diversity_bonus
where the weights come from profile['ranking'] (alpha/beta/gamma) and importance
itself is a weighted blend of cross-source mentions, source authority, recency,
engagement, and citation velocity (profile['ranking']['importance_weights']).

The diversity bonus reserves rank for academia / niche-subfield stories so the
feed never collapses into one family.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aidigest.models import Family, Story
from aidigest.process._signals import is_release_title
from aidigest.process._vec import cosine as _cosine

_FAMILY_AUTHORITY: dict[Family, float] = {
    Family.ACADEMIA: 0.9,
    Family.INDUSTRY: 0.8,
    Family.META: 0.7,
    Family.COMMUNITY: 0.6,
}

# A single-source viral post is suggestive, not conclusive: upvotes alone must NOT
# crown a Hacker News meme a "breakthrough". When a story has NO corroborating
# signal (cross-source mentions, citations, or an explicit release/announcement),
# its engagement is discounted to this fraction so it lands in NOTABLE range and can
# never reach the BREAKTHROUGH importance bar (or mark the whole day a breakthrough).
_UNCORROBORATED_ENGAGEMENT_FACTOR = 0.5


def _is_substantive(story: Story, citation: float) -> bool:
    """Does the story carry a signal of REAL significance beyond raw single-source
    virality? Cross-source corroboration, citation velocity, or a concrete
    release/announcement title all qualify; a lone viral community thread (a
    front-page meme, a personal anecdote, an institutional-drama post) does not.

    Family is intentionally NOT used: importance is scored BEFORE the curator
    reclassifies topic family, so a model story surfacing on HN is still COMMUNITY
    here — corroboration/citation/release framing are the reliable signals at this
    stage. A genuine major launch is corroborated or framed as a release; a viral
    anecdote is neither.
    """
    return story.mention_count > 1 or citation > 0.0 or is_release_title(story.title)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity (re-exported for callers/tests)."""
    return _cosine(a, b)


def personal_score(story: Story, interest_vector: list[float] | None) -> float:
    """Cosine of the story centroid to the interest vector, clamped to [0,1]."""
    if interest_vector is None or story.embedding is None:
        return 0.0
    return max(0.0, _cosine(story.embedding, interest_vector))


def importance_score(story: Story, *, profile: dict) -> float:
    """Objective newsworthiness in 0..1 — how much the world is reacting.

    Leads with the attention signals (real engagement + cross-source mentions +
    citation velocity, carried on the Story from its member items). Source
    authority and recency are GENTLE MULTIPLIERS, not additive baselines — so a
    routine fresh paper from a high-authority venue does not look important on its
    own. This is the signal the quiet-day gate trusts.
    """
    family_weights = profile.get("family_weights") or {}
    authority = float(
        family_weights.get(story.family.value, _FAMILY_AUTHORITY.get(story.family, 0.5))
    )
    mentions = _saturate(story.mention_count, k=4.0)  # cross-source corroboration
    engagement = max(0.0, min(1.0, float(story.engagement)))
    citation = max(0.0, min(1.0, float(story.citation)))

    # SUBSTANCE GATE: a single-source viral post is not a breakthrough on upvotes
    # alone. With no corroboration/citation/release framing, discount its engagement
    # so it lands in NOTABLE range — never the BREAKTHROUGH bar, and never enough to
    # mark a "breakthrough day". This is what keeps a viral HN meme out of the lead.
    if not _is_substantive(story, citation):
        engagement *= _UNCORROBORATED_ENGAGEMENT_FACTOR

    # Noisy-OR: ANY one exceptional signal (a viral *launch*, broad cross-source
    # corroboration, OR fast citations) should drive importance high on its own —
    # that is how a genuine breakthrough is DETECTED. A weighted sum capped each
    # signal's reach and silently buried single-source blockbusters (e.g. a 2.6k-pt
    # model release with mention_count=1). Mentions are slightly damped so a lone
    # cross-link can't masquerade as virality.
    attention = 1.0 - (1.0 - engagement) * (1.0 - 0.85 * mentions) * (1.0 - citation)
    modulator = 0.80 + 0.20 * authority  # 0.92..1.0 — gentle credibility nudge
    recency_blend = 0.75 + 0.25 * _recency(story)  # 0.75..1.0 — old items not zeroed
    return max(0.0, min(1.0, attention * modulator * recency_blend))


# A confirmed announcement is NEWS even with zero upvotes — a lab blog post has no
# engagement signal, yet "Anthropic signs a state government" is a notable day. Floor
# such stories into the NOTABLE band so real announcements lift the day above "quiet"
# and can lead — WITHOUT reaching the breakthrough bar (that still needs real
# attention). Kept below breakthrough_importance_override (0.55) by construction.
_ANNOUNCEMENT_IMPORTANCE_FLOOR = 0.42


def apply_announcement_floor(stories: list[Story]) -> list[Story]:
    """Raise importance for confirmed announcements to the NOTABLE floor (post-curation).

    Applied AFTER the curator has assigned the TOPIC family, so an INDUSTRY story (a
    real model/product/funding/policy announcement) or any explicit release-titled
    story registers as notable even though a press release carries no upvotes. This is
    the counterpart to the substance gate: virality must not INFLATE a meme, and a real
    announcement must not be INVISIBLE. Never raises a story to the breakthrough bar.
    """
    out: list[Story] = []
    for s in stories:
        is_announcement = s.family == Family.INDUSTRY or is_release_title(s.title)
        if is_announcement and s.importance < _ANNOUNCEMENT_IMPORTANCE_FLOOR:
            out.append(s.model_copy(update={"importance": _ANNOUNCEMENT_IMPORTANCE_FLOOR}))
        else:
            out.append(s)
    return out


def score_stories(
    stories: list[Story],
    *,
    interest_vector: list[float] | None,
    profile: dict,
    feedback_boost: dict[str, float] | None = None,
) -> list[Story]:
    """Return NEW Story copies with importance/personal/final_rank set, sorted desc."""
    if not stories:
        return []
    ranking = profile.get("ranking") or {}
    alpha = float(ranking.get("alpha", 0.5))
    beta = float(ranking.get("beta", 0.4))
    gamma = float(ranking.get("gamma", 0.1))
    boosts = feedback_boost or {}

    scored: list[Story] = []
    for story in stories:
        importance = importance_score(story, profile=profile)
        personal = personal_score(story, interest_vector)
        diversity = _diversity_bonus(story, profile)
        final = alpha * importance + beta * personal + gamma * diversity
        final += boosts.get(story.id, 0.0)
        final -= _mute_penalty(story, profile)
        scored.append(
            story.model_copy(
                update={
                    "importance": round(importance, 6),
                    "personal": round(personal, 6),
                    "final_rank": round(final, 6),
                }
            )
        )
    scored.sort(key=lambda s: s.final_rank, reverse=True)
    return scored


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _diversity_bonus(story: Story, profile: dict) -> float:
    """Reserve rank for academia / niche subfields so one family can't dominate."""
    bonus = 0.0
    if story.family == Family.ACADEMIA:
        bonus += 1.0
    family_weights = profile.get("family_weights") or {}
    bonus += float(family_weights.get(story.family.value, 0.5)) * 0.5
    return min(1.0, bonus)


def _mute_penalty(story: Story, profile: dict) -> float:
    """Down-rank stories whose title matches a muted topic."""
    mutes = [m.lower() for m in (profile.get("mutes") or [])]
    title = story.title.lower()
    return 0.5 if any(m in title for m in mutes) else 0.0


def _saturate(value: float, *, k: float) -> float:
    """Map a non-negative count to 0..1 with diminishing returns."""
    v = max(0.0, float(value))
    return v / (v + k) if (v + k) > 0 else 0.0


def _recency(story: Story) -> float:
    created = story.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age_days = max((datetime.now(UTC) - created).total_seconds() / 86400.0, 0.0)
    return float(0.5 ** (age_days / 2.0)) if age_days >= 0 else 1.0


__all__ = [
    "score_stories",
    "importance_score",
    "personal_score",
    "apply_announcement_floor",
    "cosine",
]
