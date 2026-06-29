"""Hybrid-Editorial design tokens + small HTML helpers for delivery renderers.

Single source of truth for the email/HTML look so render_md.py stays focused on
structure. Tokens mirror ACCEPTANCE.md (Source Serif headlines, IBM Plex Mono
metadata, paper-white background, near-black ink, one oxblood accent).
"""

from __future__ import annotations

import hashlib
import hmac
import html

# --- Color tokens (ACCEPTANCE.md "Hybrid Editorial") ---------------------- #
PAPER = "#FAF8F3"  # background, paper white
INK = "#1A1A1A"  # near-black body text
ACCENT = "#8B2E2E"  # deep oxblood / ink-red (the ONE accent)
MUTED = "#6B6660"  # secondary text
HAIRLINE = "#E3DED4"  # borders / rules

# --- Font stacks ---------------------------------------------------------- #
SERIF = "'Source Serif Pro', Georgia, 'Times New Roman', serif"
MONO = "'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace"

# --- Tier glyphs / labels (shared by md + html + telegram) ---------------- #
TIER_LABEL: dict[str, str] = {
    "breakthrough": "BREAKTHROUGH",
    "notable": "NOTABLE",
    "minor": "MINOR",
    "quiet_day": "QUIET",
}

FAMILY_EMOJI: dict[str, str] = {
    "academia": "🎓",
    "industry": "🏭",
    "community": "💬",
    "meta": "🗞️",
}


def esc(text: str) -> str:
    """HTML-escape a string for safe inline interpolation."""
    return html.escape(text or "", quote=True)


def feedback_signature(
    *, target_id: str, target_kind: str, signal: str, value: str, secret: str
) -> str:
    """HMAC-SHA256 (truncated) over the feedback link's signed fields.

    Used to make email feedback links unforgeable: the renderer appends the
    signature, the `GET /api/feedback/click` shim recomputes and compares it.
    Returns "" when `secret` is empty (signing disabled).
    """
    if not secret:
        return ""
    msg = f"{target_id}|{target_kind}|{signal}|{value}".encode()
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def feedback_url(
    api_base: str,
    *,
    target_id: str,
    target_kind: str,
    signal: str,
    secret: str = "",
) -> str:
    """Build a GET feedback link for email clients (they cannot POST).

    The API exposes POST /api/feedback for the web app, but email needs a
    clickable link. We point at a GET shim (`/api/feedback/click`) carrying the
    same fields as query params so a single tap records the signal. When
    `secret` is set, an HMAC `sig` is appended so the link cannot be forged.
    """
    base = (api_base or "").rstrip("/")
    value = "1" if signal == "up" else "-1"
    url = (
        f"{base}/api/feedback/click"
        f"?target_id={esc(target_id)}"
        f"&target_kind={esc(target_kind)}"
        f"&signal={signal}&value={value}"
    )
    sig = feedback_signature(
        target_id=target_id, target_kind=target_kind, signal=signal, value=value, secret=secret
    )
    if sig:
        url += f"&sig={sig}"
    return url


__all__ = [
    "PAPER",
    "INK",
    "ACCENT",
    "MUTED",
    "HAIRLINE",
    "SERIF",
    "MONO",
    "TIER_LABEL",
    "FAMILY_EMOJI",
    "esc",
    "feedback_url",
    "feedback_signature",
]
