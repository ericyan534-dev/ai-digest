"""Editorial curation — pick the smol.ai-worthy stories across all three worlds.

This is the CURATOR, not a binary AI/not-AI gate. From the day's clustered stories
it keeps only what a busy AI researcher would actually want:

  * ACADEMIA  — papers significant to the reader's subfields (novel method, strong
                result, influential work); drop incremental/unrelated arXiv noise.
  * INDUSTRY  — real lab/company announcements (model/product/research/policy).
  * COMMUNITY/META — substantive, *discussed* threads and analyses; DROP low-traction
                "Show HN" self-promo, trivial tools, one-off repos, off-topic posts.

One cheap batched LLM call; family- and signal-aware (it sees each story's family
and cross-source mention count). Skipped in mock/offline mode and when disabled.
Family BALANCE is handled downstream in generation (per-family slot reservation);
this step only decides *worthiness*.
"""

from __future__ import annotations

import json
import re

from aidigest.config import get_settings
from aidigest.llm.base import LLMClient
from aidigest.llm.factory import get_llm
from aidigest.models import Family, Story

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "keep": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "family": {"type": "string", "enum": [f.value for f in Family]},
                },
            },
        }
    },
}


def _parse_keep(raw: str, *, n: int) -> list[tuple[int, str | None]] | None:
    """Parse {"keep": [{"n", "family"}]} -> [(0-indexed idx, topic family)].

    Tolerates the legacy plain-int form ({"keep": [1, 3]}) -> family None. Returns
    None only on a parse failure (caller then keeps everything unchanged).
    """
    text = (raw or "").strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(obj, dict) or "keep" not in obj:
        return None

    seen: set[int] = set()
    out: list[tuple[int, str | None]] = []
    for item in obj.get("keep") or []:
        if isinstance(item, dict):
            try:
                idx = int(item.get("n")) - 1  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            fam = item.get("family")
            family = fam if isinstance(fam, str) else None
        else:
            try:
                idx = int(item) - 1
            except (TypeError, ValueError):
                continue
            family = None
        if 0 <= idx < n and idx not in seen:
            seen.add(idx)
            out.append((idx, family))
    return out


# Self-promo framings the reader explicitly does NOT want (when single-source).
_SELF_PROMO_PREFIXES: tuple[str, ...] = ("show hn:", "tell hn:", "ask hn:")


def _is_self_promo(story: Story) -> bool:
    """A single-source 'Show HN'/'[P]' personal-project post (drop pre-LLM).

    Cross-corroborated items (mention_count > 1) survive — if multiple sources
    picked it up, it's a real story regardless of the original framing.
    """
    if story.mention_count > 1:
        return False
    title = story.title.lower().strip()
    return title.startswith(_SELF_PROMO_PREFIXES) or "[p]" in title


# Viral-but-worthless framings for an AI *research* digest: personal anecdotes
# (medical / resume / career) that trend on HN but carry zero research/industry
# signal — e.g. "I used Claude to get a second opinion on my MRI", "My resume scored
# 90/100". High-precision so it never nukes a real story; the LLM curator handles the
# broader semantic class (institutional drama, takedown complaints, culture-war takes).
# Gated to single-source so a corroborated story is never keyword-dropped.
_NOISE_RE = re.compile(
    r"\b(my (resume|cv|mri|ct scan|x[- ]?ray|tumou?r|cancer|diagnosis|blood ?work|"
    r"salary|landlord|boss|professor|grade|exam)|second opinion)\b",
    re.IGNORECASE,
)


def _is_low_signal_noise(story: Story) -> bool:
    """Single-source viral personal-anecdote noise (drop pre-LLM, deterministically)."""
    if story.mention_count > 1:
        return False
    return bool(_NOISE_RE.search(story.title or ""))


def _listing(stories: list[Story]) -> str:
    rows: list[str] = []
    for i, s in enumerate(stories):
        mc = s.mention_count
        sig = f"mentions={mc}" if mc > 1 else "single-source"
        rows.append(f"{i + 1}. [{s.family.value}] ({sig}) {s.title.strip()}")
    return "\n".join(rows)


async def curate_stories(
    stories: list[Story], *, profile: dict, llm: LLMClient | None = None
) -> list[Story]:
    """Return the editorially-worthy subset (order preserved). No-op offline."""
    settings = get_settings()
    if settings.llm_mock or not getattr(settings, "relevance_filter", True):
        return stories
    # Hard pre-filter the explicit self-promo + viral-anecdote noise before the LLM.
    stories = [s for s in stories if not _is_self_promo(s) and not _is_low_signal_noise(s)]
    if len(stories) <= 1:
        return stories

    client = llm or get_llm()
    subfields = ", ".join(profile.get("subfields") or []) or "AI / ML / LLMs"
    prompt = (
        "You are the editor of a personal AI-research digest in the style of "
        "smol.ai / AINews. The reader is an AI researcher focused on: "
        f"{subfields}. They want a DENSE, high-signal digest spanning THREE worlds — "
        "ACADEMIA (arXiv/conferences), INDUSTRY (AI lab/company announcements, "
        "products, research, policy), and COMMUNITY (substantive discussions).\n\n"
        "From the numbered stories, return the numbers worth this reader's attention:\n"
        "- ACADEMIA: papers genuinely significant to the subfields above (novel "
        "method / strong result / influential). DROP incremental or unrelated papers.\n"
        "- INDUSTRY: real announcements from AI labs or companies. Keep these.\n"
        "- COMMUNITY/META: substantive analyses or widely-discussed threads.\n\n"
        "HARD RULES (the reader hates low-signal self-promo):\n"
        '- DROP any item whose SOURCE is self-promotional — "Show HN", "[P]" personal '
        "projects, a personal/startup blog, or a one-off GitHub repo — EVEN IF the "
        "underlying topic is interesting. If the topic is genuinely big it will also "
        "appear from a primary/lab source or a widely-discussed thread; keep THAT one.\n"
        "- DROP trivial tools, tutorials, off-topic posts, and narrow product releases "
        "unrelated to the subfields (e.g. OCR, image editing) unless they materially "
        "advance them.\n"
        "- DROP papers OUTSIDE the reader's subfields even when they use ML — e.g. ML "
        "applied to optical networks, telecom, energy grids, generic IoT, or domain "
        "verticals (medical imaging, chemistry) that do not advance LLMs / RL / agents "
        "/ the listed subfields. Keep only genuinely on-subfield research.\n"
        "- DROP pure vendor marketing / blog essays / customer case studies / EXPLAINERS "
        "with no concrete release or result — e.g. 'how agents are transforming work', "
        "'what exactly is the full stack?', 'ask an AI expert ...', or workforce / "
        "economic-impact / policy-report think-pieces. Keep the actual release or "
        "concrete deal, NOT the press essay.\n"
        "- DROP viral-but-shallow Hacker News / Reddit content that carries NO concrete "
        "AI research, product, or result — NO MATTER HOW MANY UPVOTES. This includes: "
        "personal anecdotes ('I used AI to ...', medical / resume / career stories), "
        "institutional or culture-war drama (exam cheating, fraud accusations, "
        "lawsuits-as-gossip, layoffs hot-takes), and platform / takedown / moderation "
        "complaints. A front-page meme is still a meme — high engagement is NOT worth. "
        "If there is a concrete development buried inside, keep ONLY that, not the drama.\n"
        "- A single-source community post with no discussion is NOT worth a slot.\n\n"
        "Be SELECTIVE — quality over quantity. Better 6 strong items than 12 padded.\n\n"
        "For EACH kept story, ALSO assign its TOPIC family by what the story is ABOUT "
        "(NOT where it surfaced) — this is how industry news gets into the Industry "
        "section even when it broke on HN/Reddit:\n"
        "- 'industry': an AI company or lab is the subject — a model/product/API "
        "release, hardware, funding, acquisition, partnership, benchmark, or policy "
        "(e.g. an OpenAI/Anthropic/Google/Meta/DeepSeek/Mistral announcement), EVEN if "
        "it surfaced on HN or a newsletter.\n"
        "- 'academia': a research paper / result (arXiv, conference).\n"
        "- 'community': a community discussion, debate, or tool with no single company "
        "as the subject.\n"
        "- 'meta': a curator's own newsletter/roundup (smol.ai, Latent Space, Interconnects).\n"
        'Return ONLY JSON: {"keep": [{"n": number, "family": '
        '"academia"|"industry"|"community"|"meta"}, ...]}.\n\n'
        f"{_listing(stories)}\n"
    )
    raw = await client.generate(prompt, json_schema=_SCHEMA, temperature=0.0)
    kept = _parse_keep(raw, n=len(stories))
    if kept is None:  # parse failure -> keep all (never nuke the digest)
        return stories
    out: list[Story] = []
    for idx, family in kept:
        story = stories[idx]
        if family:  # reassign to the TOPIC family for sectioning
            try:
                story = story.model_copy(update={"family": Family(family)})
            except ValueError:
                pass
        out.append(story)
    return out


__all__ = ["curate_stories"]
