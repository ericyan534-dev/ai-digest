"""Weekly-email markdown rendering (inline elements) + signed daily feedback links."""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

from aidigest.deliver.render_md import (  # noqa: E402
    _inline_md,
    _markdown_body_to_html,
    render_daily_html,
)


def test_inline_bold_italic_link_code() -> None:
    html = _inline_md("**bold** and *em* and `code` and [text](http://u)")
    assert "<strong>bold</strong>" in html
    assert "<em>em</em>" in html
    assert "<code" in html and ">code</code>" in html
    assert '<a href="http://u"' in html and ">text</a>" in html
    assert "**" not in html and "](http" not in html


def test_markdown_body_lists_and_headings() -> None:
    md = "## Heading\n\n- one\n- two\n\nA **bold** paragraph with a [link](http://x)."
    html = _markdown_body_to_html(md)
    assert "<h2" in html
    assert "<ul" in html and html.count("<li") == 2
    assert "<strong>bold</strong>" in html
    assert '<a href="http://x"' in html
    assert "**" not in html  # no raw asterisks leak into the email


def test_html_escaped_before_inline() -> None:
    # A stray angle bracket must be escaped, not emitted as a tag.
    html = _inline_md("5 < 10 and **safe**")
    assert "&lt;" in html
    assert "<strong>safe</strong>" in html


def test_daily_html_signs_links_only_with_secret(busy_daily) -> None:  # type: ignore[no-untyped-def]
    signed = render_daily_html(busy_daily, api_base="http://x", link_secret="k")
    unsigned = render_daily_html(busy_daily, api_base="http://x")
    assert "/api/feedback/click" in signed
    assert "sig=" in signed
    assert "sig=" not in unsigned
