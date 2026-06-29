"""Pydantic request models for the FastAPI layer.

Response payloads are domain models (`DailyDigest`, `Story`, ...) serialized via
`model_dump(mode="json")`; only inbound bodies need their own request schemas.
These validate untrusted client input at the system boundary (fail fast, 422).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from aidigest.models import FeedbackSignal, FeedbackTargetKind


class FeedbackRequest(BaseModel):
    """Body for `POST /api/feedback` — a 👍/👎, click, dwell, or NL instruction."""

    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    target_kind: FeedbackTargetKind
    signal: FeedbackSignal
    value: float = 1.0
    text: str | None = None


class TuneRequest(BaseModel):
    """Body for `POST /api/tune` — a natural-language feed-steering instruction."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1)


__all__ = ["FeedbackRequest", "TuneRequest"]
