"""Reddit adapter — OAuth (app-only) with a free public-RSS fallback.

Reads r/LocalLLaMA and r/MachineLearning. When ``REDDIT_CLIENT_ID`` +
``REDDIT_CLIENT_SECRET`` are set we use app-only OAuth against ``oauth.reddit.com``
(reliable, full metrics: upvotes/comments). Otherwise we fall back to the public
``/r/<sub>/hot/.rss`` feeds — which, unlike the ``.json`` API (hard 403 from
datacenter IPs), return 200 (rate-limited 429, handled by the shared retry). RSS
carries no score/comment counts, so RSS-sourced items have no engagement metric;
they still add cross-source corroboration. For reliable Reddit set up OAuth via
reddit.com/prefs/apps (a "script" app — NOT Devvit, which builds on-Reddit apps,
not a data API). Skips stickied/meta and stale items.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from aidigest.config import Settings, get_settings
from aidigest.ingest._feed import (
    entry_author,
    entry_text,
    fetch_feed,
    iter_recent_entries,
)
from aidigest.ingest._util import after, html_to_text, log, parse_dt
from aidigest.ingest.base import make_async_client, with_retry
from aidigest.models import Family, Item

SUBREDDITS: tuple[str, ...] = ("LocalLLaMA", "MachineLearning")
_LISTINGS: tuple[str, ...] = ("hot", "new")
_LIMIT = 75
_MIN_SCORE = 5
# Respectful, descriptive UA per Reddit API etiquette (no auth used).
_UA = "ai-digest/0.1 (personal news digest; +https://github.com/ai-digest)"


async def _get_oauth_token(cfg: Settings) -> str | None:
    """Fetch an app-only (client_credentials) OAuth token, or None on failure."""
    auth = httpx.BasicAuth(cfg.reddit_client_id, cfg.reddit_client_secret)

    async def _do() -> str | None:
        async with make_async_client(
            headers={"User-Agent": cfg.reddit_user_agent}, auth=auth
        ) as client:
            resp = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
            return str(token) if token else None

    try:
        return await with_retry(_do)
    except Exception as exc:  # noqa: BLE001 — fall back to public JSON
        log.warning("reddit OAuth token failed: %s", exc)
        return None


async def _fetch_listing(
    client: Any, sub: str, listing: str, *, base: str, suffix: str
) -> list[dict]:
    url = f"{base}/r/{sub}/{listing}{suffix}"

    async def _do() -> list[dict]:
        resp = await client.get(url, params={"limit": _LIMIT, "raw_json": 1})
        resp.raise_for_status()
        data = resp.json()
        children = data.get("data", {}).get("children", [])
        return [c.get("data", {}) for c in children if isinstance(c, dict)]

    return await with_retry(_do)


def _post_to_item(post: dict) -> Item | None:
    if post.get("stickied") or post.get("hidden"):
        return None
    title = (post.get("title") or "").strip()
    if not title:
        return None
    permalink = post.get("permalink")
    comments_url = f"https://www.reddit.com{permalink}" if permalink else None
    external = post.get("url_overridden_by_dest") or post.get("url")
    url = comments_url or external
    published = parse_dt(post.get("created_utc"))
    selftext = html_to_text(post.get("selftext") or "", max_len=4000)
    metrics = {
        "upvotes": int(post.get("score") or post.get("ups") or 0),
        "comments": int(post.get("num_comments") or 0),
    }
    return Item.create(
        source="reddit",
        family=Family.COMMUNITY,
        title=title,
        url=url,
        author=post.get("author"),
        published_at=published,
        raw_text=selftext,
        metrics=metrics,
        raw={
            "subreddit": post.get("subreddit"),
            "reddit_id": post.get("id"),
            "external_url": external,
            "flair": post.get("link_flair_text"),
        },
    )


class RedditAdapter:
    """AI community posts from a fixed set of subreddits (public JSON, no auth)."""

    name = "reddit"
    family = Family.COMMUNITY

    async def fetch(self, since: datetime) -> list[Item]:
        cfg = get_settings()
        token: str | None = None
        if cfg.reddit_oauth_enabled:
            token = await _get_oauth_token(cfg)
        if token:
            return await self._fetch_via_oauth(cfg, token, since)
        # No OAuth: free public RSS (200, rate-limited) instead of .json (403).
        return await self._fetch_via_rss(cfg, since)

    async def _fetch_via_oauth(
        self, cfg: Settings, token: str, since: datetime
    ) -> list[Item]:
        seen: set[str] = set()
        items: list[Item] = []
        headers = {"User-Agent": cfg.reddit_user_agent, "Authorization": f"bearer {token}"}
        try:
            async with make_async_client(headers=headers) as client:
                for sub in SUBREDDITS:
                    for listing in _LISTINGS:
                        try:
                            posts = await _fetch_listing(
                                client, sub, listing, base="https://oauth.reddit.com", suffix=""
                            )
                        except Exception as exc:
                            log.warning("reddit r/%s/%s failed: %s", sub, listing, exc)
                            continue
                        for post in posts:
                            item = self._accept(post, since, seen)
                            if item is not None:
                                items.append(item)
        except Exception as exc:  # noqa: BLE001
            log.warning("reddit oauth fetch failed: %s", exc)
        return items

    async def _fetch_via_rss(self, cfg: Settings, since: datetime) -> list[Item]:
        """Free fallback: parse the public hot/.rss feeds (no auth, no metrics)."""
        seen: set[str] = set()
        items: list[Item] = []
        for sub in SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/hot/.rss"
            try:
                parsed = await fetch_feed(url)
            except Exception as exc:  # noqa: BLE001 — rate-limit/transient; fail-soft
                log.warning("reddit RSS r/%s failed: %s", sub, exc)
                continue
            for entry, published in iter_recent_entries(parsed, since):
                try:
                    title = (entry.get("title") or "").strip()
                    link = entry.get("link") or None
                    if not title or not link or link in seen:
                        continue
                    seen.add(link)
                    items.append(
                        Item.create(
                            source="reddit",
                            family=Family.COMMUNITY,
                            title=title,
                            url=link,
                            author=entry_author(entry),
                            published_at=published,
                            raw_text=entry_text(entry, max_len=2000),
                            raw={"subreddit": sub, "via": "rss"},
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("reddit RSS skipped a bad entry: %s", exc)
        return items

    @staticmethod
    def _accept(post: dict, since: datetime, seen: set[str]) -> Item | None:
        try:
            rid = str(post.get("id") or "")
            if rid and rid in seen:
                return None
            if int(post.get("score") or 0) < _MIN_SCORE:
                return None
            item = _post_to_item(post)
            if item is None or not after(item.published_at, since):
                return None
            if rid:
                seen.add(rid)
            return item
        except Exception as exc:
            log.warning("reddit skipped a bad post: %s", exc)
            return None


ADAPTER = RedditAdapter()

__all__ = ["ADAPTER", "RedditAdapter", "SUBREDDITS"]
