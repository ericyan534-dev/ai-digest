"""Karpathy-wiki Markdown export — digests as a linked knowledge graph.

Renders digests into Obsidian-style notes so issues compose into a personal wiki:

    wiki/
      daily/2026-06-25.md         # daily issue: TL;DR + sections, links to story notes
      weekly/weekly-2026-W26.md   # weekly editorial, links to the week's dailies
      stories/<slug>.md           # one note per story: takeaway, why, #subfield tags

Every story note carries `#subfield` tags (e.g. #reinforcement-learning-for-nlp),
so Obsidian's tag pages aggregate everything about a subfield over time — turning
the daily firehose into a browsable, backlinked second brain.

Pure render functions return ``{relative_path: markdown}``; ``export_*`` writes
them under a wiki directory. No LLM, no network.
"""

from __future__ import annotations

from pathlib import Path

from aidigest.models import (
    DailyDigest,
    StorySummary,
    WeeklyDigest,
    WeeklyShortlistEntry,
    slugify,
)

WIKI_TAG = "ai-digest"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _tagify(text: str) -> str:
    """A subfield/family string -> an Obsidian-safe tag token (no spaces)."""
    return slugify(text) or "untagged"


def _story_slug(s: StorySummary) -> str:
    return slugify(s.title) or s.story_id


def _wikilink(slug: str, alias: str) -> str:
    """An Obsidian wikilink `[[path/slug|Alias]]`."""
    return f"[[{slug}|{alias.strip()}]]"


def _frontmatter(fields: dict[str, object]) -> str:
    """Minimal YAML frontmatter block (lists rendered as `- item`)."""
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _shortlist_block(title: str, entries: list[WeeklyShortlistEntry]) -> list[str]:
    if not entries:
        return []
    out = [f"## {title}"]
    for e in entries:
        link = f"[{e.title.strip()}]({e.url})" if e.url else e.title.strip()
        out.append(f"- {link} — {e.one_liner.strip()}")
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# Story note (one per StorySummary)
# --------------------------------------------------------------------------- #


def _story_note(s: StorySummary, *, backlink: str) -> str:
    tags = sorted({_tagify(t) for t in s.tags} | {_tagify(s.family.value)})
    fm = _frontmatter(
        {
            "title": s.title.strip(),
            "type": "story",
            "family": s.family.value,
            "tier": s.tier.value,
            "tags": [WIKI_TAG, *tags],
        }
    )
    body: list[str] = [fm, "", f"# {s.title.strip()}", ""]
    if s.takeaway.strip():
        body.append(s.takeaway.strip())
    if s.why_it_matters.strip():
        body.append(f"\n**Why it matters:** {s.why_it_matters.strip()}")
    if s.links:
        body.append("\n## Sources")
        body.extend(f"- {u}" for u in s.links)
    inline = " ".join(f"#{t}" for t in tags)
    if inline:
        body.append(f"\n{inline}")
    body.append(f"\nFrom {backlink}")
    return "\n".join(body).strip() + "\n"


# --------------------------------------------------------------------------- #
# Daily issue note (+ its story notes)
# --------------------------------------------------------------------------- #


def render_daily_wiki(digest: DailyDigest) -> dict[str, str]:
    """Render a DailyDigest to {path: markdown}: one daily issue note linking to
    one story note per summary (each tagged by subfield)."""
    notes: dict[str, str] = {}
    date = digest.date
    backlink = _wikilink(f"daily/{date}", f"Daily {date}")
    all_tags: set[str] = set()
    body: list[str] = []

    for section in digest.sections:
        body.append(f"## {section.heading.strip()}")
        if not section.summaries:
            body.append("_Nothing notable._\n")
            continue
        for s in section.summaries:
            slug = f"stories/{_story_slug(s)}"
            notes[f"{slug}.md"] = _story_note(s, backlink=backlink)
            all_tags |= {_tagify(t) for t in s.tags}
            body.append(f"- {_wikilink(slug, s.title)} · `{s.tier.value}`")
            if s.takeaway.strip():
                body.append(f"  - {s.takeaway.strip()}")
        body.append("")

    fm = _frontmatter(
        {
            "title": f"Daily — {date}",
            "type": "daily",
            "date": date,
            "tier": digest.overall_tier.value,
            "quiet": digest.quiet_day,
            "tags": [WIKI_TAG, "daily", *sorted(all_tags)],
        }
    )
    head: list[str] = [
        fm,
        "",
        f"# Daily — {date}",
        "",
        f"`{digest.overall_tier.value}` · {len(digest.story_ids)} stories",
        "",
    ]
    if digest.tldr.strip():
        head.append(f"> {digest.tldr.strip()}\n")
    if digest.quiet_day and not digest.sections:
        head.append("_Quiet day — nothing major shipped._")

    notes[f"daily/{date}.md"] = "\n".join([*head, *body]).strip() + "\n"
    return notes


# --------------------------------------------------------------------------- #
# Weekly issue note
# --------------------------------------------------------------------------- #


def render_weekly_wiki(
    digest: WeeklyDigest, *, daily_dates: list[str] | None = None
) -> dict[str, str]:
    """Render a WeeklyDigest to {path: markdown}: the editorial issue note, with
    its shortlist/radar and `[[daily/...]]` links to the week's editions."""
    fm = _frontmatter(
        {
            "title": digest.title.strip() or "Week at a Glance",
            "type": "weekly",
            "week_of": digest.week_of,
            "tier": digest.overall_tier.value,
            "quiet": digest.quiet_week,
            "tags": [WIKI_TAG, "weekly"],
        }
    )
    lines: list[str] = [
        fm,
        "",
        f"# {digest.title.strip() or 'Week at a Glance'}",
        "",
    ]
    if digest.lede.strip():
        lines.append(f"*{digest.lede.strip()}*\n")
    lines.append(f"`{digest.overall_tier.value}` · Week of {digest.week_of}\n")
    if digest.quiet_week:
        lines.append("_Quiet week — little of consequence shipped._\n")
    if digest.body_markdown.strip():
        lines.append(digest.body_markdown.strip())
        lines.append("")
    lines.extend(_shortlist_block("What I'd actually read this week", digest.shortlist))
    lines.extend(_shortlist_block("On my radar", digest.on_my_radar))
    if daily_dates:
        lines.append("## This week's editions")
        lines.extend(
            f"- {_wikilink(f'daily/{d}', f'Daily {d}')}" for d in sorted(daily_dates)
        )

    return {f"weekly/{digest.id}.md": "\n".join(lines).strip() + "\n"}


# --------------------------------------------------------------------------- #
# Writing to disk
# --------------------------------------------------------------------------- #


def export_notes(notes: dict[str, str], *, wiki_dir: str | Path) -> list[Path]:
    """Write {relative_path: content} under `wiki_dir`; return written paths."""
    root = Path(wiki_dir)
    written: list[Path] = []
    for rel, content in notes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def export_daily(digest: DailyDigest, *, wiki_dir: str | Path) -> list[Path]:
    return export_notes(render_daily_wiki(digest), wiki_dir=wiki_dir)


def export_weekly(
    digest: WeeklyDigest, *, wiki_dir: str | Path, daily_dates: list[str] | None = None
) -> list[Path]:
    return export_notes(
        render_weekly_wiki(digest, daily_dates=daily_dates), wiki_dir=wiki_dir
    )


__all__ = [
    "render_daily_wiki",
    "render_weekly_wiki",
    "export_notes",
    "export_daily",
    "export_weekly",
]
