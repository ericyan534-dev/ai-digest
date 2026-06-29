"""Semantic Scholar adapter — citation-velocity enrichment (Graph API).

Semantic Scholar's Academic Graph API is an *enrichment* source, not a discovery
feed: given an arXiv id (or S2 paper id) it returns citation counts and a
recent-citation velocity — a strong "this matters" signal layered onto arXiv /
HF papers in the ranking stage.

As an Adapter it satisfies the Protocol but `fetch(since)` returns [] (there is
no time-windowed discovery query that respects S2 rate limits). The real entry
points are `enrich_item()` (returns a NEW Item with metrics) and
`citation_velocity()`. Public, key-optional; we add the key header when present.

API: https://api.semanticscholar.org/graph/v1
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aidigest.config import get_settings
from aidigest.ingest._util import log, parse_dt
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.models import Family, Item

_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_FIELDS = "citationCount,influentialCitationCount,year,publicationDate,citations.year"


def _paper_id(item: Item) -> str | None:
    """Resolve an S2-queryable id from an Item (prefers arXiv id)."""
    arxiv_id = item.raw.get("arxiv_id")
    if arxiv_id:
        return f"arXiv:{arxiv_id}"
    if item.url and "arxiv.org/abs/" in item.url:
        return f"arXiv:{item.url.rsplit('/abs/', 1)[-1].split('v')[0]}"
    s2_id = item.raw.get("s2_paper_id")
    if s2_id:
        return str(s2_id)
    return None


def _auth_headers() -> dict[str, str]:
    key = getattr(get_settings(), "semantic_scholar_api_key", "") or ""
    return {"x-api-key": key} if key else {}


def citation_velocity(paper: dict, *, as_of: datetime | None = None) -> float:
    """Citations accrued in the last ~24 months as a per-year rate.

    A cheap proxy for "is this paper accelerating": recent citations / window.
    Robust to missing fields; returns 0.0 when unknown.
    """
    citations = paper.get("citations") or []
    now_year = (as_of or datetime.now()).year
    window = 2
    recent = 0
    for c in citations:
        try:
            year = c.get("year")
            if isinstance(year, int) and now_year - year <= window:
                recent += 1
        except (AttributeError, TypeError):
            continue
    if recent:
        return round(recent / window, 3)
    # Fall back to total/age if per-citation years are absent.
    total = paper.get("citationCount") or 0
    pub_year = paper.get("year")
    if isinstance(pub_year, int) and pub_year <= now_year:
        age = max(now_year - pub_year, 1)
        return round(total / age, 3)
    return float(total)


async def _fetch_paper(client: Any, paper_id: str) -> dict | None:
    async def _do() -> dict | None:
        resp = await client.get(
            f"{_BASE}/{paper_id}", params={"fields": _FIELDS}
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return dict(resp.json())

    return await with_retry(_do)


async def enrich_item(item: Item, *, client: Any | None = None) -> Item:
    """Return a NEW Item with citation metrics merged in; original on failure."""
    paper_id = _paper_id(item)
    if not paper_id:
        return item
    try:
        if client is not None:
            paper = await _fetch_paper(client, paper_id)
        else:
            async with make_async_client(headers=_auth_headers()) as owned:
                paper = await _fetch_paper(owned, paper_id)
    except Exception as exc:
        log.warning("semantic_scholar enrich failed for %s: %s", paper_id, exc)
        return item
    if not paper:
        return item
    velocity = citation_velocity(paper, as_of=item.published_at)
    metrics = {
        **item.metrics,
        "citations": int(paper.get("citationCount") or 0),
        "influential_citations": int(paper.get("influentialCitationCount") or 0),
        "citation_velocity": velocity,
    }
    raw = {**item.raw, "s2_publication_date": paper.get("publicationDate")}
    return item.model_copy(update={"metrics": metrics, "raw": raw})


async def enrich_items(items: list[Item]) -> list[Item]:
    """Enrich a batch of academia items (best-effort, never raises)."""
    out: list[Item] = []
    try:
        async with make_async_client(headers=_auth_headers()) as client:
            for it in items:
                out.append(await enrich_item(it, client=client))
    except Exception as exc:
        log.warning("semantic_scholar batch enrich failed: %s", exc)
        return items
    return out


class SemanticScholarAdapter:
    """Enrichment-only adapter; `fetch` is a no-op (returns [])."""

    name = "semantic_scholar"
    family = Family.ACADEMIA

    async def fetch(self, since: datetime) -> list[Item]:
        # S2 is an enrichment source, not a discovery feed. Discovery happens via
        # arxiv/hf_papers; enrichment is applied in the processing stage. We keep
        # `since` only to satisfy the Adapter Protocol.
        _ = parse_dt(since)  # keep import referenced; harmless no-op
        return []


ADAPTER = SemanticScholarAdapter()

__all__ = [
    "ADAPTER",
    "SemanticScholarAdapter",
    "citation_velocity",
    "enrich_item",
    "enrich_items",
]
