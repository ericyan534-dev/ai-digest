"""Render DailyDigest / WeeklyDigest to clean Markdown and Hybrid-Editorial HTML.

These are pure functions over the frozen digest models — no I/O, no LLM. The
Markdown is reused by Telegram, a future wiki export, and tests; the HTML is the
email body (serif headlines, mono meta, paper-white, oxblood accent).

The flexibility principle is honored at render time too: a quiet digest renders
its honest TL;DR prominently and keeps sections terse; breakthroughs keep their
full-depth takeaway verbatim (the generator already sized the prose by tier).
"""

from __future__ import annotations

import re

from aidigest.deliver.style import (
    ACCENT,
    FAMILY_EMOJI,
    HAIRLINE,
    INK,
    MONO,
    MUTED,
    PAPER,
    SERIF,
    TIER_LABEL,
    esc,
    feedback_url,
)
from aidigest.models import (
    DailyDigest,
    DigestSection,
    StorySummary,
    WeeklyDigest,
    WeeklyShortlistEntry,
)

# Default API base for inline email feedback links (overridable per call).
DEFAULT_API_BASE = "http://localhost:8000"


# --------------------------------------------------------------------------- #
# Markdown — daily
# --------------------------------------------------------------------------- #


def _tier_tag(tier: str) -> str:
    return TIER_LABEL.get(tier, tier.upper())


_LINK_LABELS: tuple[tuple[str, str], ...] = (
    ("arxiv.org", "arXiv"),
    ("huggingface.co", "HF"),
    ("openreview.net", "OpenReview"),
    ("reddit.com", "Reddit"),
    ("news.ycombinator.com", "HN"),
    ("github.com", "GitHub"),
)


def _link_label(url: str) -> str:
    """A short, source-aware anchor (arXiv / HF / Reddit / domain) — never bare 'link'."""
    low = url.lower()
    for needle, label in _LINK_LABELS:
        if needle in low:
            return label
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else "link"


def _summary_md(s: StorySummary) -> str:
    """A full lead item (tier tag + takeaway + why-it-matters + tags + links)."""
    lines: list[str] = []
    tag = _tier_tag(s.tier.value)
    lines.append(f"**{s.title.strip()}** · `{tag}`")
    if s.takeaway.strip():
        lines.append(s.takeaway.strip())
    if s.why_it_matters.strip():
        lines.append(f"*Why it matters:* {s.why_it_matters.strip()}")
    if s.tags:
        lines.append("Tags: " + ", ".join(f"`{t}`" for t in s.tags))
    if s.links:
        lines.append(" · ".join(f"[{_link_label(u)}]({u})" for u in s.links))
    return "\n\n".join(lines)


def _section_heading(section: DigestSection) -> str:
    """Heading with the family emoji — unless it already starts with one (e.g. the
    cross-family '⚡ Top Stories' section), in which case it is used as-is."""
    h = section.heading.strip()
    if h and ord(h[0]) > 0x2000:  # already begins with an emoji/symbol
        return h
    emoji = FAMILY_EMOJI.get(section.family.value, "")
    return f"{emoji} {h}".strip()


def _brief_md(s: StorySummary) -> str:
    """A one-line trend item: title + source-aware links (no takeaway)."""
    links = " · ".join(f"[{_link_label(u)}]({u})" for u in s.links)
    return f"- **{s.title.strip()}**" + (f" — {links}" if links else "")


def _section_md(section: DigestSection) -> str:
    parts = [f"## {_section_heading(section)}"]
    if section.intro.strip():
        parts.append(section.intro.strip())
    full = [s for s in section.summaries if s.takeaway.strip()]
    brief = [s for s in section.summaries if not s.takeaway.strip()]
    for s in full:
        parts.append(_summary_md(s))
    if brief:
        parts.append("\n".join(_brief_md(s) for s in brief))
    if not section.intro.strip() and not section.summaries:
        parts.append("_Nothing notable._")
    return "\n\n".join(parts)


def render_daily_md(digest: DailyDigest) -> str:
    """Render a DailyDigest to clean Markdown (Telegram / wiki / tests)."""
    head = f"# Daily — {digest.date}"
    meta = f"`{_tier_tag(digest.overall_tier.value)}` · {len(digest.story_ids)} stories"
    tldr = f"> {digest.tldr.strip()}" if digest.tldr.strip() else ""

    blocks: list[str] = [head, meta, tldr]
    if digest.quiet_day and not digest.sections:
        blocks.append("_Quiet day — nothing major shipped._")
    for section in digest.sections:
        blocks.append(_section_md(section))

    return "\n\n".join(b for b in blocks if b).strip() + "\n"


# --------------------------------------------------------------------------- #
# Markdown — weekly
# --------------------------------------------------------------------------- #


def _shortlist_md(title: str, entries: list[WeeklyShortlistEntry]) -> str:
    if not entries:
        return ""
    lines = [f"## {title}"]
    for e in entries:
        link = f"[{e.title.strip()}]({e.url})" if e.url else e.title.strip()
        lines.append(f"- {link} — {e.one_liner.strip()}")
    return "\n".join(lines)


def render_weekly_md(digest: WeeklyDigest) -> str:
    """Render a WeeklyDigest to clean Markdown (Telegram / wiki / tests)."""
    head = f"# {digest.title.strip()}"
    meta = f"`{_tier_tag(digest.overall_tier.value)}` · Week of {digest.week_of}"
    lede = f"_{digest.lede.strip()}_" if digest.lede.strip() else ""

    blocks: list[str] = [head, meta, lede]
    if digest.quiet_week:
        blocks.append("_Quiet week — little of consequence shipped._")
    if digest.body_markdown.strip():
        blocks.append(digest.body_markdown.strip())
    blocks.append(_shortlist_md("What I'd actually read this week", digest.shortlist))
    blocks.append(_shortlist_md("On my radar", digest.on_my_radar))

    return "\n\n".join(b for b in blocks if b).strip() + "\n"


# --------------------------------------------------------------------------- #
# Telegram — condensed plain text (push channel; full digest in email/web)
# --------------------------------------------------------------------------- #


def render_telegram_text(
    digest: DailyDigest, *, per_section: int = 3, limit: int = 3500
) -> str:
    """Condensed PLAIN-TEXT daily for Telegram, mirroring the digest's sections.

    Telegram caps messages at 4096 chars and its MarkdownV2 escaping is fragile, so
    the push is short plain text: header + honest TL;DR + per-section headings with
    the lead items (tier-tagged) and a few trend titles (NO tier tag — they are not
    being featured as notable). Capping per section keeps any one family (e.g. a
    long academia paper list) from dominating. Full takeaways live in email/web.
    """
    lines: list[str] = [
        f"🗞️ AI Digest — {digest.date}  ·  {_tier_tag(digest.overall_tier.value)}"
    ]
    if digest.tldr.strip():
        lines += ["", digest.tldr.strip()]

    for sec in digest.sections:
        lines += ["", _section_heading(sec)]
        leads = [s for s in sec.summaries if s.takeaway.strip()]
        brief = [s for s in sec.summaries if not s.takeaway.strip()]
        for s in leads:  # featured items keep their tier tag
            lines.append(f"• [{_tier_tag(s.tier.value)}] {s.title.strip()}")
        for s in brief[:per_section]:  # trend items: plain title, capped
            lines.append(f"• {s.title.strip()}")

    if not digest.sections and digest.quiet_day:
        lines += ["", "Nothing notable today."]
    lines += ["", "Full takeaways → your email / the web dashboard."]

    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- #
# HTML — shared shell
# --------------------------------------------------------------------------- #


def _html_shell(*, title: str, inner: str) -> str:
    """Wrap inner HTML in a paper-white, serif, email-safe document."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
</head>
<body style="margin:0;padding:0;background:{PAPER};">
<div style="max-width:640px;margin:0 auto;padding:32px 24px;background:{PAPER};
  color:{INK};font-family:{SERIF};font-size:17px;line-height:1.6;">
{inner}
<hr style="border:none;border-top:1px solid {HAIRLINE};margin:32px 0 16px;">
<p style="font-family:{MONO};font-size:12px;color:{MUTED};margin:0;">
  ai-digest · a smol.ai for one
</p>
</div>
</body>
</html>"""


def _meta_line(text: str) -> str:
    return (
        f'<p style="font-family:{MONO};font-size:12px;letter-spacing:0.04em;'
        f'text-transform:uppercase;color:{MUTED};margin:0 0 4px;">{text}</p>'
    )


def _tier_pill(tier: str) -> str:
    tag = _tier_tag(tier)
    return (
        f'<span style="font-family:{MONO};font-size:11px;color:{ACCENT};'
        f'border:1px solid {HAIRLINE};border-radius:3px;padding:1px 6px;">{tag}</span>'
    )


def _feedback_links_html(
    api_base: str, *, target_id: str, target_kind: str, link_secret: str = ""
) -> str:
    up = feedback_url(
        api_base, target_id=target_id, target_kind=target_kind, signal="up", secret=link_secret
    )
    down = feedback_url(
        api_base, target_id=target_id, target_kind=target_kind, signal="down", secret=link_secret
    )
    style = (
        f"font-family:{MONO};font-size:14px;text-decoration:none;"
        f"color:{ACCENT};margin-right:12px;"
    )
    return (
        f'<p style="margin:8px 0 0;">'
        f'<a href="{up}" style="{style}">👍 helpful</a>'
        f'<a href="{down}" style="{style}">👎 skip</a>'
        f"</p>"
    )


# --------------------------------------------------------------------------- #
# HTML — daily
# --------------------------------------------------------------------------- #


def _summary_html(s: StorySummary, api_base: str, link_secret: str = "") -> str:
    links = ""
    if s.links:
        anchors = " · ".join(
            f'<a href="{esc(u)}" style="color:{ACCENT};">{esc(_link_label(u))}</a>'
            for u in s.links
        )
        links = (
            f'<p style="font-family:{MONO};font-size:13px;color:{MUTED};margin:6px 0 0;">'
            f"{anchors}</p>"
        )
    why = ""
    if s.why_it_matters.strip():
        why = (
            f'<p style="margin:6px 0 0;color:{MUTED};">'
            f'<em>Why it matters:</em> {esc(s.why_it_matters.strip())}</p>'
        )
    tags = ""
    if s.tags:
        chips = " ".join(
            f'<span style="font-family:{MONO};font-size:11px;color:{MUTED};">#{esc(t)}</span>'
            for t in s.tags
        )
        tags = f'<p style="margin:6px 0 0;">{chips}</p>'
    return (
        f'<div style="margin:0 0 24px;padding:0 0 20px;border-bottom:1px solid {HAIRLINE};">'
        f'<h3 style="font-family:{SERIF};font-size:20px;font-weight:600;margin:0 0 6px;">'
        f"{esc(s.title.strip())} &nbsp;{_tier_pill(s.tier.value)}</h3>"
        f'<p style="margin:0;">{esc(s.takeaway.strip())}</p>'
        f"{why}{tags}{links}"
        f"{_feedback_links_html(api_base, target_id=s.story_id, target_kind='story', link_secret=link_secret)}"
        f"</div>"
    )


def _brief_html(s: StorySummary) -> str:
    """A one-line trend item (title + source-aware links) for a section list."""
    anchors = " · ".join(
        f'<a href="{esc(u)}" style="color:{ACCENT};">{esc(_link_label(u))}</a>'
        for u in s.links
    )
    suffix = f" — {anchors}" if anchors else ""
    return f'<li style="margin:0 0 6px;"><strong>{esc(s.title.strip())}</strong>{suffix}</li>'


def _section_html(section: DigestSection, api_base: str, link_secret: str = "") -> str:
    head = (
        f'<h2 style="font-family:{SERIF};font-size:15px;text-transform:uppercase;'
        f'letter-spacing:0.08em;color:{ACCENT};border-bottom:2px solid {ACCENT};'
        f'padding-bottom:4px;margin:32px 0 16px;">{esc(_section_heading(section))}</h2>'
    )
    if not section.intro.strip() and not section.summaries:
        return head + f'<p style="color:{MUTED};margin:0;"><em>Nothing notable.</em></p>'

    intro = ""
    if section.intro.strip():
        intro = f'<p style="margin:0 0 16px;color:{INK};">{esc(section.intro.strip())}</p>'
    full = [s for s in section.summaries if s.takeaway.strip()]
    brief = [s for s in section.summaries if not s.takeaway.strip()]
    body = intro + "".join(_summary_html(s, api_base, link_secret) for s in full)
    if brief:
        items = "".join(_brief_html(s) for s in brief)
        body += f'<ul style="padding-left:20px;margin:0 0 16px;">{items}</ul>'
    return head + body


def render_daily_html(
    digest: DailyDigest, *, api_base: str | None = None, link_secret: str = ""
) -> str:
    """Render a DailyDigest to a Hybrid-Editorial HTML email body.

    `link_secret` (when set) signs the inline 👍/👎 email links so the GET click
    shim can reject forged feedback.
    """
    base = api_base or DEFAULT_API_BASE
    title = f"Daily — {digest.date}"
    header = (
        _meta_line(f"Daily · {digest.date} · {_tier_tag(digest.overall_tier.value)}")
        + f'<h1 style="font-family:{SERIF};font-size:30px;font-weight:700;'
        f'margin:0 0 12px;line-height:1.25;">{esc(digest.tldr.strip())}</h1>'
    )
    if digest.quiet_day and not digest.sections:
        body = (
            f'<p style="color:{MUTED};font-style:italic;margin:0;">'
            f"Quiet day — nothing major shipped.</p>"
        )
    else:
        body = "".join(_section_html(s, base, link_secret) for s in digest.sections)
    return _html_shell(title=title, inner=header + body)


# --------------------------------------------------------------------------- #
# HTML — weekly
# --------------------------------------------------------------------------- #


def _shortlist_html(title: str, entries: list[WeeklyShortlistEntry]) -> str:
    if not entries:
        return ""
    items = []
    for e in entries:
        head = (
            f'<a href="{esc(e.url)}" style="color:{ACCENT};font-weight:600;">{esc(e.title)}</a>'
            if e.url
            else f"<strong>{esc(e.title)}</strong>"
        )
        items.append(
            f'<li style="margin:0 0 8px;">{head} — '
            f'<span style="color:{MUTED};">{esc(e.one_liner.strip())}</span></li>'
        )
    return (
        f'<h2 style="font-family:{SERIF};font-size:15px;text-transform:uppercase;'
        f'letter-spacing:0.08em;color:{ACCENT};margin:32px 0 12px;">{esc(title)}</h2>'
        f'<ul style="padding-left:20px;margin:0;">{"".join(items)}</ul>'
    )


_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![*_])[*_]([^*_\n]+)[*_](?![*_])")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline_md(text: str) -> str:
    """Escape, then apply inline markdown: links, **bold**, *italic*, `code`.

    Email-safe: every replacement emits inline-styled tags only. Bold runs before
    italic so ``**x**`` is not mis-read as two italics.
    """
    out = esc(text)
    out = _LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" style="color:{ACCENT};">{m.group(1)}</a>', out
    )
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _ITALIC_RE.sub(r"<em>\1</em>", out)
    out = _CODE_RE.sub(
        lambda m: f'<code style="font-family:{MONO};font-size:14px;">{m.group(1)}</code>', out
    )
    return out


def _markdown_body_to_html(body_markdown: str) -> str:
    """Markdown->HTML for the weekly editorial body (email-safe inline elements).

    Handles headings, unordered lists, and inline bold/italic/links/code so the
    editorial reads as prose — not raw ``**asterisks**`` — in an email client.
    The canonical render is still the Markdown; the web app uses a full parser.
    """
    blocks = [b.strip() for b in body_markdown.strip().split("\n\n") if b.strip()]
    out: list[str] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and all(ln.startswith(("- ", "* ")) for ln in lines):
            items = "".join(
                f'<li style="margin:0 0 6px;">{_inline_md(ln[2:].strip())}</li>' for ln in lines
            )
            out.append(f'<ul style="padding-left:20px;margin:0 0 16px;">{items}</ul>')
        elif block.startswith("### "):
            out.append(
                f'<h3 style="font-family:{SERIF};font-size:17px;margin:18px 0 8px;">'
                f"{_inline_md(block[4:].strip())}</h3>"
            )
        elif block.startswith("## "):
            out.append(
                f'<h2 style="font-family:{SERIF};font-size:20px;margin:20px 0 10px;">'
                f"{_inline_md(block[3:].strip())}</h2>"
            )
        elif block.startswith("# "):
            out.append(
                f'<h1 style="font-family:{SERIF};font-size:26px;margin:24px 0 12px;">'
                f"{_inline_md(block[2:].strip())}</h1>"
            )
        else:
            paragraph = "<br>".join(_inline_md(ln) for ln in lines)
            out.append(f'<p style="margin:0 0 16px;">{paragraph}</p>')
    return "".join(out)


def render_weekly_html(digest: WeeklyDigest) -> str:
    """Render a WeeklyDigest to a Hybrid-Editorial HTML email body."""
    title = digest.title.strip() or "Week at a Glance"
    header = (
        _meta_line(
            f"Week at a Glance · {digest.week_of} · {_tier_tag(digest.overall_tier.value)}"
        )
        + f'<h1 style="font-family:{SERIF};font-size:32px;font-weight:700;'
        f'margin:0 0 8px;line-height:1.2;">{esc(title)}</h1>'
        + f'<p style="font-size:19px;color:{MUTED};font-style:italic;margin:0 0 24px;">'
        f"{esc(digest.lede.strip())}</p>"
    )
    quiet = ""
    if digest.quiet_week:
        quiet = (
            f'<p style="color:{MUTED};font-style:italic;margin:0 0 16px;">'
            f"Quiet week — little of consequence shipped.</p>"
        )
    body = _markdown_body_to_html(digest.body_markdown)
    shortlist = _shortlist_html("What I'd actually read this week", digest.shortlist)
    radar = _shortlist_html("On my radar", digest.on_my_radar)
    return _html_shell(title=title, inner=header + quiet + body + shortlist + radar)


__all__ = [
    "render_daily_md",
    "render_weekly_md",
    "render_daily_html",
    "render_weekly_html",
    "DEFAULT_API_BASE",
]
