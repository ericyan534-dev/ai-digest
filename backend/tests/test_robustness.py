"""Robustness logic: arXiv-id academia clustering, announcement lead-sort, telegram tags."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

from aidigest.deliver.render_md import render_telegram_text  # noqa: E402
from aidigest.generate.daily import (  # noqa: E402
    _announce_score,
    _clip,
    _clip_to_sentence,
    _dedupe_leads,
    _is_dup_lead,
    _lead_sort_key,
)
from aidigest.models import (  # noqa: E402
    DailyDigest,
    DigestSection,
    Family,
    ImportanceTier,
    Item,
    Story,
    StorySummary,
)
from aidigest.process.cluster import (  # noqa: E402
    _cannot_link,
    _url_path,
    cluster_into_stories,
)


def _emb(i: int, dim: int = 1536) -> list[float]:
    v = [0.0] * dim
    v[i] = 1.0
    return v


def _acad(arxiv: str, vec: list[float], *, source: str = "arxiv", url: str | None = None) -> Item:
    return Item.create(
        source=source,
        family=Family.ACADEMIA,
        title=f"Paper {arxiv}",
        url=url or f"https://arxiv.org/abs/{arxiv}",
        raw_text="abstract",
        published_at=datetime.now(UTC),
        raw={"arxiv_id": arxiv},
    ).with_embedding(vec)


# --------------------------------------------------------------------------- #
# arXiv-id academia clustering
# --------------------------------------------------------------------------- #


def test_distinct_academia_papers_do_not_merge() -> None:
    # Identical embeddings but DIFFERENT arXiv ids -> must stay separate.
    a = _acad("2606.00001", _emb(0))
    b = _acad("2606.00002", _emb(0))
    stories = cluster_into_stories([a, b], threshold=0.86)
    assert len(stories) == 2


def test_same_paper_two_sources_merges() -> None:
    # Same arXiv id from arxiv + HF -> one story, mention_count 2.
    a = _acad("2606.00001", _emb(0))
    b = _acad("2606.00001", _emb(0), source="hf_papers", url="https://huggingface.co/papers/2606.00001")
    stories = cluster_into_stories([a, b], threshold=0.86)
    assert len(stories) == 1
    assert stories[0].mention_count == 2


# --------------------------------------------------------------------------- #
# Same-source over-merge guard (distinct articles from one feed stay apart)
# --------------------------------------------------------------------------- #


def _news(source: str, url: str, vec: list[float]) -> Item:
    return Item.create(
        source=source,
        family=Family.INDUSTRY,
        title=url.rsplit("/", 1)[-1],
        url=url,
        raw_text="body",
        published_at=datetime.now(UTC),
    ).with_embedding(vec)


def test_url_path_strips_host_and_query() -> None:
    # Same post mirrored on two hosts (+ tracking query) -> same identity key.
    old = "https://old.reddit.com/r/X/comments/1ufo0un/title/?utm=1"
    www = "https://www.reddit.com/r/X/comments/1ufo0un/title/"
    assert _url_path(old) == _url_path(www)
    # Distinct articles from one outlet -> different keys.
    assert _url_path("https://tc.com/2026/06/25/a/") != _url_path("https://tc.com/2026/06/25/b/")
    assert _url_path(None) is None


def test_same_source_distinct_articles_do_not_merge() -> None:
    # Three TechCrunch funding pieces with IDENTICAL embeddings must stay three
    # stories — not one "Patronus $50M" bundle that hides $2.3B and $15M raises.
    a = _news("rss:techcrunch-ai", "https://tc.com/2026/06/25/patronus-50m/", _emb(0))
    b = _news("rss:techcrunch-ai", "https://tc.com/2026/06/25/general-intuitions-2-3b/", _emb(0))
    c = _news("rss:techcrunch-ai", "https://tc.com/2026/06/25/netris-15m/", _emb(0))
    assert _cannot_link(a, b) and _cannot_link(a, c) and _cannot_link(b, c)
    stories = cluster_into_stories([a, b, c], threshold=0.86)
    assert len(stories) == 3


def test_same_post_mirrored_hosts_still_merge() -> None:
    # The SAME reddit post on old. and www. (one source, same path) -> one story.
    old = _news("reddit", "https://old.reddit.com/r/X/comments/1ufo0un/t/", _emb(0))
    www = _news("reddit", "https://www.reddit.com/r/X/comments/1ufo0un/t/", _emb(0))
    assert not _cannot_link(old, www)
    stories = cluster_into_stories([old, www], threshold=0.86)
    assert len(stories) == 1
    assert stories[0].mention_count == 2


def test_cross_source_same_story_still_eligible() -> None:
    # Different sources never blocked — that's the corroboration case clustering wants.
    tc = _news("rss:techcrunch-ai", "https://tc.com/2026/06/25/gpt56-gating/", _emb(0))
    rd = _news("reddit", "https://www.reddit.com/r/X/comments/1ufo0un/gpt56/", _emb(0))
    assert not _cannot_link(tc, rd)
    stories = cluster_into_stories([tc, rd], threshold=0.86)
    assert len(stories) == 1


# --------------------------------------------------------------------------- #
# Announcement lead-sort (depth-priority)
# --------------------------------------------------------------------------- #


def test_clip_no_dangling_comma() -> None:
    # A clause-boundary cut must not end on a comma — it trails an ellipsis instead.
    long = (
        "State restrictions mean the most capable agentic backbones remain "
        "inaccessible for public benchmarking, forcing the community to optimize "
        "smaller open-weight models for their planning and tool-use workflows."
    )
    out = _clip(long, 130)
    assert not out.rstrip().endswith(","), out
    assert out.endswith("…")


def test_clip_prefers_sentence_end() -> None:
    text = "First full sentence here is plenty long. Then a second clause continues on and on."
    out = _clip(text, 60)
    assert out == "First full sentence here is plenty long."  # clean period, no ellipsis


def test_clip_to_sentence_never_cuts_mid_sentence() -> None:
    # Two full sentences end early (~46% of the limit); a third is half-written. The
    # intro clip must keep the two complete sentences, NOT cut into the third with "…".
    text = (
        "Korean giants committed $550B to memory while Arena became a $100M business. "
        "That reshapes the eval market. To secure critical footprints the labs are now "
        "racing to lock in long-term compute and data partnerships across regions."
    )
    out = _clip_to_sentence(text, 200)
    assert out.endswith("business. That reshapes the eval market.")
    assert "…" not in out
    assert "To secure" not in out  # the half-started third sentence is dropped


def test_announce_score() -> None:
    assert _announce_score("OpenAI unveils LLM-optimized inference chip") == 1.0
    assert _announce_score("Introducing computer use in Gemini") == 1.0
    assert _announce_score("How agents are transforming work") == 0.0
    assert _announce_score("Why does everyone hate AI") == 0.0
    assert _announce_score("DeepSeek V4 benchmark results") == 0.4


def _industry_story(title: str) -> Story:
    return Story(
        id=title[:10], title=title, family=Family.INDUSTRY,
        item_ids=["i"], representative_item_id="i",
        importance=0.4, tier=ImportanceTier.NOTABLE,
    )


def test_lead_sort_prioritizes_announcement_over_essay() -> None:
    essay = _industry_story("How agents are transforming work")
    release = _industry_story("OpenAI unveils inference chip")
    ordered = sorted([essay, release], key=_lead_sort_key, reverse=True)
    assert ordered[0].title.startswith("OpenAI unveils")


# --------------------------------------------------------------------------- #
# Cross-source lead dedup (same news from two outlets, embedded below cluster bar)
# --------------------------------------------------------------------------- #


def _lead(
    sid: str, title: str, vec: list[float], *, tier: ImportanceTier = ImportanceTier.NOTABLE
) -> Story:
    return Story(
        id=sid, title=title, family=Family.INDUSTRY,
        item_ids=[f"item-{sid}"], representative_item_id=f"item-{sid}",
        embedding=vec, importance=0.5, tier=tier,
    )


def test_dedupe_leads_collapses_cross_source_duplicate() -> None:
    # Same story, two outlets, identical centroid -> one lead; the duplicate's link
    # (item id) is folded in (never dropped) and its id is reported as absorbed.
    tc = _lead("tc", "White House asks OpenAI to slow-roll new model", _emb(0))
    rd = _lead("rd", "US Govt to individually approve who gets GPT 5.6", _emb(0))
    kept, absorbed = _dedupe_leads([tc, rd])
    assert len(kept) == 1
    assert absorbed == {"rd"}
    assert set(kept[0].item_ids) == {"item-tc", "item-rd"}  # link preserved on merge
    assert kept[0].mention_count == 2


def test_dedupe_leads_keeps_distinct_stories() -> None:
    a = _lead("a", "OpenAI ships inference chip", _emb(0))
    b = _lead("b", "Mistral raises Series C", _emb(1))  # orthogonal -> cosine 0
    kept, absorbed = _dedupe_leads([a, b])
    assert len(kept) == 2
    assert absorbed == set()


def test_dedupe_leads_never_collapses_two_breakthroughs() -> None:
    # Even at identical embeddings, two breakthroughs each keep their own lead.
    a = _lead("a", "GLM 5.2 released", _emb(0), tier=ImportanceTier.BREAKTHROUGH)
    b = _lead("b", "Claude Fable 5 released", _emb(0), tier=ImportanceTier.BREAKTHROUGH)
    assert not _is_dup_lead(b, a)
    kept, absorbed = _dedupe_leads([a, b])
    assert len(kept) == 2


# --------------------------------------------------------------------------- #
# Telegram: tier tags only on featured (lead) items, capped per section
# --------------------------------------------------------------------------- #


def test_telegram_tags_only_leads() -> None:
    lead = StorySummary(
        story_id="1", title="Big Release", family=Family.INDUSTRY,
        tier=ImportanceTier.NOTABLE, takeaway="a full takeaway", why_it_matters="",
    )
    papers = [
        StorySummary(
            story_id=str(i), title=f"Paper {i}", family=Family.ACADEMIA,
            tier=ImportanceTier.NOTABLE, takeaway="", why_it_matters="",
        )
        for i in range(6)
    ]
    digest = DailyDigest(
        id="daily-x", date="2026-06-26", tldr="Test day", overall_tier=ImportanceTier.NOTABLE,
        sections=[
            DigestSection(family=Family.INDUSTRY, heading="Industry", summaries=[lead]),
            DigestSection(family=Family.ACADEMIA, heading="Academia", summaries=papers),
        ],
    )
    text = render_telegram_text(digest)
    assert "[NOTABLE] Big Release" in text  # featured lead keeps tier tag
    assert "Paper 0" in text
    assert "[NOTABLE] Paper 0" not in text  # trend papers are NOT marked notable
    # academia papers are capped per section (default 3), not all 6 shown
    assert sum(f"Paper {i}" in text for i in range(6)) <= 3
