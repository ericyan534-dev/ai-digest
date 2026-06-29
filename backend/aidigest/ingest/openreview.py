"""OpenReview adapter — API v2 (ICLR / NeurIPS / ACL-adjacent submissions).

OpenReview exposes conference submissions, scores, and reviews weeks before
camera-ready — the academia edge smol.ai lacks. We query the v2 notes endpoint
for recent submissions to the venues the user cares about, robust to the API's
nested {"value": ...} content fields. No key required for public submissions.

API: https://api2.openreview.net/notes  (docs: https://docs.openreview.net)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aidigest.ingest._util import after, html_to_text, log, parse_dt
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.models import Family, Item

_API = "https://api2.openreview.net/notes"
# Venue invitation prefixes to harvest; '${YEAR}' is filled at query time.
VENUE_INVITATIONS: tuple[str, ...] = (
    "ICLR.cc/{year}/Conference/-/Submission",
    "NeurIPS.cc/{year}/Conference/-/Submission",
    "aclweb.org/ACL/{year}/Conference/-/Submission",
)
_LIMIT = 100


def _val(field: Any) -> Any:
    """OpenReview v2 wraps content values as {'value': X}; unwrap defensively."""
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def _note_to_item(note: dict) -> Item | None:
    content = note.get("content") or {}
    title = str(_val(content.get("title")) or "").strip()
    if not title:
        return None
    note_id = note.get("id")
    url = f"https://openreview.net/forum?id={note_id}" if note_id else None
    abstract = html_to_text(str(_val(content.get("abstract")) or ""), max_len=6000)
    authors_raw = _val(content.get("authors"))
    author = None
    if isinstance(authors_raw, list) and authors_raw:
        author = ", ".join(str(a) for a in authors_raw[:6])
    elif authors_raw:
        author = str(authors_raw)
    # cdate (creation) is epoch milliseconds in OpenReview.
    cdate = note.get("cdate") or note.get("tcdate")
    published = parse_dt(cdate / 1000) if isinstance(cdate, int | float) else None
    venue = str(_val(content.get("venue")) or "")
    return Item.create(
        source="openreview",
        family=Family.ACADEMIA,
        title=title,
        url=url,
        author=author,
        published_at=published,
        raw_text=abstract,
        metrics={},
        raw={
            "openreview_id": note_id,
            "venue": venue,
            "invitation": note.get("invitation") or note.get("invitations"),
        },
    )


async def _query_invitation(client: Any, invitation: str) -> list[dict]:
    async def _do() -> list[dict]:
        resp = await client.get(
            _API,
            params={
                "invitation": invitation,
                "limit": _LIMIT,
                "sort": "cdate:desc",
            },
        )
        resp.raise_for_status()
        return list(resp.json().get("notes", []))

    return await with_retry(_do)


class OpenReviewAdapter:
    """Recent submissions to ICLR / NeurIPS / ACL via OpenReview API v2."""

    name = "openreview"
    family = Family.ACADEMIA

    async def fetch(self, since: datetime) -> list[Item]:
        years = {since.year, since.year + 1}  # review seasons span year boundary
        invitations = [
            tpl.format(year=y) for tpl in VENUE_INVITATIONS for y in sorted(years)
        ]
        seen: set[str] = set()
        items: list[Item] = []
        try:
            async with make_async_client() as client:
                for invitation in invitations:
                    try:
                        notes = await _query_invitation(client, invitation)
                    except Exception as exc:
                        log.warning("openreview %s failed: %s", invitation, exc)
                        continue
                    for note in notes:
                        item = self._accept(note, since, seen)
                        if item is not None:
                            items.append(item)
        except Exception as exc:
            log.warning("openreview adapter failed: %s", exc)
        return items

    @staticmethod
    def _accept(note: dict, since: datetime, seen: set[str]) -> Item | None:
        try:
            nid = str(note.get("id") or "")
            if nid and nid in seen:
                return None
            item = _note_to_item(note)
            if item is None:
                return None
            if not after(item.published_at, since):
                return None
            if nid:
                seen.add(nid)
            return item
        except Exception as exc:
            log.warning("openreview skipped a bad note: %s", exc)
            return None


ADAPTER = OpenReviewAdapter()

__all__ = ["ADAPTER", "OpenReviewAdapter", "VENUE_INVITATIONS"]
