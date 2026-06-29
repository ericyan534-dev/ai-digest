"""RSS feed catalog — lab blogs (industry) + curator newsletters (meta).

The registry builds one `RSSAdapter` per row. Each row is
`(slug, url, family)`. Lab blogs are INDUSTRY; newsletters/curators are META.
URLs are public RSS/Atom endpoints; unreachable feeds degrade to empty (the
adapter logs and skips), so a dead feed never breaks the batch.
"""

from __future__ import annotations

from aidigest.models import Family

# (slug, url, family). slug becomes the adapter name "rss:<slug>".
# URLs verified live 2026-06-25. Anthropic and The Batch publish NO public RSS
# (all candidate endpoints 404/500) — they need a web-reader/scrape path, tracked
# as a follow-up; omitted here so the batch isn't polluted with dead-feed noise.
FEEDS: tuple[tuple[str, str, Family], ...] = (
    # --- Lab / company blogs (industry; verified live 2026-06-26) ---
    ("openai", "https://openai.com/news/rss.xml", Family.INDUSTRY),
    ("deepmind", "https://deepmind.google/blog/rss.xml", Family.INDUSTRY),
    ("google-ai", "https://blog.google/technology/ai/rss/", Family.INDUSTRY),
    ("mistral", "https://mistral.ai/rss.xml", Family.INDUSTRY),
    ("qwen", "https://qwenlm.github.io/blog/index.xml", Family.INDUSTRY),
    ("huggingface", "https://huggingface.co/blog/feed.xml", Family.INDUSTRY),
    ("nvidia-ai", "https://blogs.nvidia.com/blog/category/deep-learning/feed/", Family.INDUSTRY),
    ("together", "https://www.together.ai/blog/rss.xml", Family.INDUSTRY),
    # --- AI tech press (industry news DAILY — labs post only ~weekly, the curator
    #     + topic-reclassification filter these to substance) ---
    ("techcrunch-ai", "https://techcrunch.com/category/artificial-intelligence/feed/", Family.INDUSTRY),
    ("venturebeat-ai", "https://venturebeat.com/category/ai/feed/", Family.INDUSTRY),
    ("theverge-ai", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", Family.INDUSTRY),
    # --- Top newsletters / curators (meta) ---
    ("latent-space", "https://www.latent.space/feed", Family.META),
    ("import-ai", "https://importai.substack.com/feed", Family.META),
    ("interconnects", "https://www.interconnects.ai/feed", Family.META),
)

__all__ = ["FEEDS"]
