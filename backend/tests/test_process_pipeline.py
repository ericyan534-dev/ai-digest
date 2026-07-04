"""Tests for the process stage: embed -> cluster -> enrich (+ signals/vec).

Mock LLM. Uses injected one-hot embeddings (via conftest helpers) so clustering
is deterministic and does not depend on the random mock embedder.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aidigest.models import Family, Item
from aidigest.process._signals import (
    authority,
    citation_velocity,
    engagement_score,
    recency_score,
)
from aidigest.process._vec import centroid, cosine, cosine_matrix
from aidigest.process.cluster import cluster_into_stories, story_id
from aidigest.process.embed import embed_items, embedding_text
from aidigest.process.enrich import enrich_stories, story_citation_velocity

NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _item(idx: int, *, source: str, family: Family, title: str, vec_index: int,
          metrics: dict | None = None) -> Item:
    from tests.conftest import unit_basis_vector  # type: ignore

    return Item.create(
        source=source, family=family, title=title, url=f"https://e/{idx}",
        raw_text=f"body {title}", published_at=NOW, metrics=metrics or {},
    ).with_embedding(unit_basis_vector(vec_index))


# ----------------------------------------------------------------- _vec


def test_cosine_basic() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_matrix_diagonal_is_one() -> None:
    m = cosine_matrix([[1.0, 0.0], [0.0, 1.0]])
    assert m[0, 0] == pytest.approx(1.0)
    assert m[0, 1] == pytest.approx(0.0)


def test_centroid_normalizes() -> None:
    c = centroid([[1.0, 0.0], [1.0, 0.0]])
    assert c is not None
    assert cosine(c, [1.0, 0.0]) == pytest.approx(1.0)
    assert centroid([]) is None


# ----------------------------------------------------------------- signals


def test_signals_ranges() -> None:
    # `recency_score` decays against the real wall clock, so a "just-published"
    # item must use a live timestamp (not the fixed NOW constant) to stay fresh
    # regardless of the calendar date the suite runs on.
    hot = Item.create(source="hn", family=Family.COMMUNITY, title="x",
                      published_at=datetime.now(UTC),
                      metrics={"upvotes": 5000, "comments": 1000})
    cold = Item.create(source="hn", family=Family.COMMUNITY, title="y", published_at=NOW)
    assert 0.0 < engagement_score(hot) <= 1.0
    assert engagement_score(cold) == 0.0
    assert authority(Item.create(source="arxiv", family=Family.ACADEMIA,
                                 title="p", published_at=NOW)) >= 0.85
    # A just-published item scores near 1.0 (exponential decay, 2-day half-life).
    assert recency_score(hot) > 0.8
    paper = Item.create(source="arxiv", family=Family.ACADEMIA, title="p",
                        published_at=NOW, metrics={"citations": 20})
    assert citation_velocity(paper) > 0.0


# ----------------------------------------------------------------- embed


@pytest.mark.asyncio
async def test_embed_items_fills_missing_only(llm) -> None:
    a = Item.create(source="hn", family=Family.COMMUNITY, title="A", published_at=NOW)
    b = Item.create(source="hn", family=Family.COMMUNITY, title="B",
                    published_at=NOW).with_embedding([0.0] * 1536)
    out = await embed_items([a, b], llm=llm)
    assert out[0].embedding is not None and len(out[0].embedding) == 1536
    assert out[1].embedding == [0.0] * 1536  # untouched
    assert await embed_items([], llm=llm) == []


def test_embedding_text_uses_title_and_lead() -> None:
    it = Item.create(source="hn", family=Family.COMMUNITY, title="T",
                     raw_text="L" * 5000, published_at=NOW)
    text = embedding_text(it)
    assert text.startswith("T\n")
    assert len(text) < 5000  # lead truncated
    bare = Item.create(source="hn", family=Family.COMMUNITY, title="OnlyTitle",
                       published_at=NOW)
    assert embedding_text(bare) == "OnlyTitle"


# ----------------------------------------------------------------- cluster


def test_cluster_groups_same_vector() -> None:
    items = [
        _item(0, source="hn", family=Family.COMMUNITY, title="DeepSeek", vec_index=0),
        _item(1, source="arxiv", family=Family.ACADEMIA, title="DeepSeek paper", vec_index=0),
        _item(2, source="reddit", family=Family.COMMUNITY, title="Other", vec_index=50),
    ]
    stories = cluster_into_stories(items, threshold=0.75)
    assert len(stories) == 2
    big = max(stories, key=lambda s: s.mention_count)
    assert big.mention_count == 2
    assert big.embedding is not None and len(big.embedding) == 1536


def test_cluster_complete_link_does_not_chain() -> None:
    """The core anti-chaining guarantee: A~B and B~C but A≁C must NOT merge into
    one story (single-link would; complete-link must not)."""
    import math

    def vec(deg: float) -> list[float]:
        r = math.radians(deg)
        return [math.cos(r), math.sin(r)]

    # cos(A,B)=cos(22.8°)≈.92, cos(B,C)=cos(22.8°)≈.92, cos(A,C)=cos(45.6°)≈.70
    a = Item.create(source="hn", family=Family.COMMUNITY, title="A",
                    published_at=NOW).with_embedding(vec(0))
    b = Item.create(source="arxiv", family=Family.ACADEMIA, title="B",
                    published_at=NOW).with_embedding(vec(22.8))
    c = Item.create(source="reddit", family=Family.COMMUNITY, title="C",
                    published_at=NOW).with_embedding(vec(45.6))
    stories = cluster_into_stories([a, b, c], threshold=0.86)
    # {A,B} merge (.92 ≥ .86); C stays separate (A–C .70 < .86). No chaining.
    assert len(stories) == 2
    assert sorted(s.mention_count for s in stories) == [1, 2]


def test_cluster_empty_and_singletons() -> None:
    assert cluster_into_stories([]) == []
    bare = Item.create(source="hn", family=Family.COMMUNITY, title="no-embed",
                       published_at=NOW)
    stories = cluster_into_stories([bare])
    assert len(stories) == 1
    assert stories[0].mention_count == 1


def test_story_id_is_stable_and_order_independent() -> None:
    assert story_id("DeepSeek V4", ["b", "a"]) == story_id("DeepSeek V4", ["a", "b"])
    assert story_id("X", ["a"]) != story_id("Y", ["a"])


# ----------------------------------------------------------------- enrich


@pytest.mark.asyncio
async def test_enrich_never_drops_stories(llm) -> None:
    items = [
        _item(0, source="arxiv", family=Family.ACADEMIA, title="P1", vec_index=0,
              metrics={"citations": 10}),
        _item(1, source="hf_papers", family=Family.ACADEMIA, title="P2", vec_index=0,
              metrics={"citations": 5}),
    ]
    stories = cluster_into_stories(items, threshold=0.5)
    items_by_id = {it.id: it for it in items}
    enriched = await enrich_stories(stories, items_by_id, llm=llm)
    assert len(enriched) == len(stories)
    assert await enrich_stories([], {}, llm=llm) == []
    assert story_citation_velocity(stories[0], items_by_id) > 0.0


@pytest.mark.asyncio
async def test_enrich_skips_single_community_story(llm) -> None:
    item = _item(0, source="reddit", family=Family.COMMUNITY, title="solo", vec_index=3)
    stories = cluster_into_stories([item], threshold=0.75)
    enriched = await enrich_stories(stories, {item.id: item}, llm=llm)
    # Single community story is not re-titled (no LLM call) => title unchanged.
    assert enriched[0].title == "solo"


@pytest.mark.asyncio
async def test_enrich_never_rewrites_titles(llm) -> None:
    # A MULTI-SOURCE story must keep a REAL source title verbatim — never an
    # LLM-invented headline (which once turned "not much happened today" into
    # "AI News Summary Reports Minimal Industry Activity").
    a = _item(0, source="smol.ai", family=Family.META, title="not much happened today",
              vec_index=0)
    b = _item(1, source="rss:latent-space", family=Family.META,
              title="[AINews] not much happened today", vec_index=0)
    stories = cluster_into_stories([a, b], threshold=0.5)
    assert stories[0].mention_count == 2  # multi-source -> old code would re-title
    enriched = await enrich_stories(stories, {a.id: a, b.id: b}, llm=llm)
    assert enriched[0].title in {"not much happened today", "[AINews] not much happened today"}


# --------------------------------------------------------------------------- #
# Fix 2 — is_release_title: "rolls out" / "rolling out" verbs
# --------------------------------------------------------------------------- #


def test_is_release_title_rolls_out() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("OpenAI rolls out GPT-5o to all users") is True


def test_is_release_title_rolling_out() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("Anthropic is rolling out Claude 4 to enterprise") is True


def test_is_release_title_drops_not_a_release() -> None:
    """'drops' must NOT be treated as a release verb (too many false positives)."""
    from aidigest.process._signals import is_release_title

    assert is_release_title("Nvidia stock drops 10% after earnings miss") is False


# --------------------------------------------------------------------------- #
# Fix 3 — is_release_title: deal / funding patterns
# --------------------------------------------------------------------------- #


def test_is_release_title_raises_with_amount() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("Anthropic raises $4B from Google") is True


def test_is_release_title_raises_without_amount_not_flagged() -> None:
    """'raises' without a $ amount must NOT be treated as a deal headline."""
    from aidigest.process._signals import is_release_title

    assert is_release_title("Study raises questions about LLM evals") is False


def test_is_release_title_accuracy_drops_not_flagged() -> None:
    """'drops' in a metrics context must not be a false positive."""
    from aidigest.process._signals import is_release_title

    assert is_release_title("Ask HN: why did my accuracy drop after fine-tuning") is False


def test_is_release_title_acquires() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("Google acquires AI startup for $500M") is True


def test_is_release_title_acquisition_of() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("Meta completes acquisition of Scale AI") is True


def test_is_release_title_partners_with() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("OpenAI partners with Palantir for government AI") is True


def test_is_release_title_signs_deal() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("Mistral signs a deal with Microsoft Azure") is True


def test_is_release_title_invests_with_amount() -> None:
    from aidigest.process._signals import is_release_title

    assert is_release_title("SoftBank invests $3B in OpenAI's next round") is True


# --------------------------------------------------------------------------- #
# Fix: cross-family academia/community cannot-link (2026-07-03 bug)
# An HF-papers ACADEMIA item and a Reddit COMMUNITY post about a DIFFERENT paper
# must never cluster together even when their embeddings happen to be similar.
# --------------------------------------------------------------------------- #


def test_cannot_link_blocks_academia_reddit_different_paper() -> None:
    """Regression for 2026-07-03 digest: 'Multi-Resolution Flow Matching' story
    showed a Reddit link pointing at the unrelated 'BlockPilot' paper because
    similar topic embeddings caused two distinct items to cluster.

    _cannot_link must return True for this cross-family, different-paper pair.
    """
    from aidigest.process.cluster import _cannot_link  # noqa: PLC2701
    from tests.conftest import unit_basis_vector  # type: ignore

    hf = Item.create(
        source="hf-papers",
        family=Family.ACADEMIA,
        title="Multi-Resolution Flow Matching: Training-Free Diffusion Acceleration via Staged Sampling",
        url="https://huggingface.co/papers/2607.01642",
        published_at=NOW,
    ).with_embedding(unit_basis_vector(0))

    reddit = Item.create(
        source="reddit-localllama",
        family=Family.COMMUNITY,
        title="BlockPilot: Instance-Adaptive Policy Learning for Efficient Block-Sparse Attention",
        url="https://www.reddit.com/r/LocalLLaMA/comments/1umgb79/blockpilot_instanceadaptive_policy_learning_for/",
        published_at=NOW,
    ).with_embedding(unit_basis_vector(0))  # identical vector — would merge without fix

    assert _cannot_link(hf, reddit) is True
    assert _cannot_link(reddit, hf) is True  # symmetric


def test_cluster_academia_reddit_different_paper_stays_separate() -> None:
    """Two distinct papers — one from HF-papers, one a Reddit thread — must produce
    two separate stories even when their embeddings are identical (threshold=0.5)."""
    from tests.conftest import unit_basis_vector  # type: ignore

    hf = Item.create(
        source="hf-papers",
        family=Family.ACADEMIA,
        title="Multi-Resolution Flow Matching: Training-Free Diffusion Acceleration via Staged Sampling",
        url="https://huggingface.co/papers/2607.01642",
        published_at=NOW,
    ).with_embedding(unit_basis_vector(0))

    reddit = Item.create(
        source="reddit-localllama",
        family=Family.COMMUNITY,
        title="BlockPilot: Instance-Adaptive Policy Learning for Efficient Block-Sparse Attention",
        url="https://www.reddit.com/r/LocalLLaMA/comments/1umgb79/blockpilot_instanceadaptive_policy_learning_for/",
        published_at=NOW,
    ).with_embedding(unit_basis_vector(0))

    stories = cluster_into_stories([hf, reddit], threshold=0.5)
    assert len(stories) == 2, (
        "Distinct papers must not be merged: story titles were "
        + str([s.title for s in stories])
    )


def test_cluster_academia_reddit_same_paper_may_merge() -> None:
    """A Reddit thread whose title clearly discusses the SAME HF paper must still be
    allowed to cluster with it — the fix must not over-block valid cross-source merges."""
    from aidigest.process.cluster import _cannot_link  # noqa: PLC2701
    from tests.conftest import unit_basis_vector  # type: ignore

    hf = Item.create(
        source="hf-papers",
        family=Family.ACADEMIA,
        title="Multi-Resolution Flow Matching: Training-Free Diffusion Acceleration via Staged Sampling",
        url="https://huggingface.co/papers/2607.01642",
        published_at=NOW,
    ).with_embedding(unit_basis_vector(0))

    reddit = Item.create(
        source="reddit-localllama",
        family=Family.COMMUNITY,
        title="Multi-Resolution Flow Matching - Training-Free Diffusion paper discussion",
        url="https://www.reddit.com/r/MachineLearning/comments/abc123/multi_resolution_flow_matching/",
        published_at=NOW,
    ).with_embedding(unit_basis_vector(0))

    # Title Jaccard ≈ 0.58 >> _PAPER_TITLE_OVERLAP_MIN → NOT cannot-link.
    assert _cannot_link(hf, reddit) is False
    stories = cluster_into_stories([hf, reddit], threshold=0.5)
    assert len(stories) == 1, "Same-paper cross-source pair should still cluster"
