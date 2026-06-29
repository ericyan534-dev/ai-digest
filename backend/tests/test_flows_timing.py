"""Tests for the pipeline step-timing helper + smoke_live guard rails."""

from __future__ import annotations

import logging

import pytest

from aidigest.flows._timing import step


@pytest.mark.asyncio
async def test_step_logs_ok(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="aidigest.flows"):
        async with step("unit") as s:
            s.set(items=3)
    text = caplog.text
    assert "step=unit status=start" in text
    assert "step=unit status=ok" in text
    assert "items=3" in text


@pytest.mark.asyncio
async def test_step_logs_error_and_reraises(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="aidigest.flows"):  # noqa: SIM117
        with pytest.raises(ValueError):
            async with step("boom"):
                raise ValueError("nope")
    assert "step=boom status=error" in caplog.text


@pytest.mark.asyncio
async def test_smoke_live_requires_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_live as smoke

    # MOCK is on in the test env -> the live smoke must refuse to run.
    with pytest.raises(SystemExit) as exc:
        await smoke._run_live()
    assert exc.value.code == 2
