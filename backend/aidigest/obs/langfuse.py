"""Optional Langfuse observability — a cheap no-op unless configured.

When LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are set AND the `langfuse` package
is installed, LLM generations are traced to Langfuse (cost / latency / quality,
plus a home for LLM-as-judge scores). Otherwise every call is a no-op, so
Langfuse is NEVER a hard dependency — it stays commented in requirements.txt and
the pipeline runs identically without it.

Observability must never break the product: every client call is wrapped so a
Langfuse outage or version mismatch is swallowed, not propagated.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from aidigest.config import get_settings


class Tracer(Protocol):
    """Minimal tracer surface used by the LLM client."""

    enabled: bool

    def generation(
        self,
        *,
        name: str,
        model: str,
        prompt: str,
        output: str,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def flush(self) -> None: ...


class _NoopTracer:
    """Does nothing — the default when Langfuse is unconfigured/absent."""

    enabled = False

    def generation(self, **_: Any) -> None:  # noqa: D401 - no-op
        return None

    def flush(self) -> None:
        return None


class _LangfuseTracer:
    """Thin adapter over a real Langfuse client; failures are swallowed."""

    enabled = True

    def __init__(self, client: Any) -> None:
        self._client = client

    def generation(
        self,
        *,
        name: str,
        model: str,
        prompt: str,
        output: str,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._client.generation(
                name=name,
                model=model,
                input=prompt,
                output=output,
                usage=usage,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001 — observability never breaks the pipeline
            return None

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001
            return None


@lru_cache(maxsize=1)
def get_tracer() -> Tracer:
    """Return the process-wide tracer: real Langfuse if configured, else no-op."""
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return _NoopTracer()
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or None,
        )
        return _LangfuseTracer(client)
    except Exception:  # noqa: BLE001 — missing package / bad config => no-op
        return _NoopTracer()


__all__ = ["Tracer", "get_tracer"]
