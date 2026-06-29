"""Tests for the Karpathy-wiki Markdown export (deliver/wiki_export.py)."""

from __future__ import annotations

from aidigest.deliver.wiki_export import (
    export_daily,
    render_daily_wiki,
    render_weekly_wiki,
)


def test_daily_wiki_makes_issue_and_story_notes(busy_daily) -> None:
    notes = render_daily_wiki(busy_daily)
    # one daily issue note + one story note per summary (2 in the busy fixture)
    assert "daily/2026-06-21.md" in notes
    story_keys = [k for k in notes if k.startswith("stories/")]
    assert len(story_keys) == 2
    assert "stories/deepseek-v4-released.md" in notes


def test_daily_issue_note_links_stories_and_tags(busy_daily) -> None:
    notes = render_daily_wiki(busy_daily)
    issue = notes["daily/2026-06-21.md"]
    # frontmatter
    assert issue.startswith("---\n")
    assert "type: daily" in issue
    assert "tier: breakthrough" in issue
    # subfield tag (slugified) propagated to the issue frontmatter
    assert "rl-for-nlp" in issue
    # wikilink to the story note, with the TL;DR quoted
    assert "[[stories/deepseek-v4-released|DeepSeek V4 released]]" in issue
    assert "> DeepSeek V4 resets the open-model frontier" in issue


def test_story_note_has_tags_why_and_backlink(busy_daily) -> None:
    note = render_daily_wiki(busy_daily)["stories/deepseek-v4-released.md"]
    assert "type: story" in note
    assert "**Why it matters:**" in note
    assert "## Sources" in note
    assert "#rl-for-nlp" in note  # inline Obsidian tag
    assert "From [[daily/2026-06-21|Daily 2026-06-21]]" in note  # backlink


def test_quiet_daily_renders_honestly(quiet_daily) -> None:
    notes = render_daily_wiki(quiet_daily)
    issue = notes["daily/2026-06-22.md"]
    assert "quiet: true" in issue
    assert "Quiet day — nothing major shipped." in issue
    assert not [k for k in notes if k.startswith("stories/")]  # no story notes


def test_weekly_wiki_links_dailies_and_shortlist(sample_weekly) -> None:
    notes = render_weekly_wiki(
        sample_weekly, daily_dates=["2026-06-15", "2026-06-16"]
    )
    assert "weekly/weekly-2026-W25.md" in notes
    issue = notes["weekly/weekly-2026-W25.md"]
    assert "# The week reasoning got cheap" in issue
    assert "## What I'd actually read this week" in issue
    assert "## On my radar" in issue
    assert "## This week's editions" in issue
    assert "[[daily/2026-06-15|Daily 2026-06-15]]" in issue


def test_export_daily_writes_files(busy_daily, tmp_path) -> None:
    written = export_daily(busy_daily, wiki_dir=tmp_path)
    assert written
    assert (tmp_path / "daily" / "2026-06-21.md").exists()
    assert (tmp_path / "stories" / "deepseek-v4-released.md").exists()
    # content round-trips
    assert "# Daily — 2026-06-21" in (tmp_path / "daily" / "2026-06-21.md").read_text()
