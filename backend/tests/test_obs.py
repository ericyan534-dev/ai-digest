"""Optional Langfuse tracer: no-op when unconfigured, and never raises."""

from __future__ import annotations

from aidigest.obs.langfuse import get_tracer


def test_tracer_is_noop_when_unconfigured() -> None:
    get_tracer.cache_clear()
    tracer = get_tracer()
    assert tracer.enabled is False
    # The no-op accepts the real client call shape and never raises.
    tracer.generation(
        name="gemini.generate", model="m", prompt="p", output="o",
        usage={"prompt_tokens": 1, "output_tokens": 2},
    )
    tracer.flush()


def test_get_tracer_is_cached() -> None:
    get_tracer.cache_clear()
    assert get_tracer() is get_tracer()
