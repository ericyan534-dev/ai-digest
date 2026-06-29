"""Loop-2/Loop-3 closure: persisted interest vector + steered profile override."""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import aidigest.api.main as api  # noqa: E402
import aidigest.flows.pipeline as pipeline  # noqa: E402


@pytest.mark.asyncio
async def test_app_state_round_trip(fake_repo) -> None:  # type: ignore[no-untyped-def]
    await fake_repo.save_interest_vector([0.1, 0.2, 0.3])
    assert await fake_repo.get_interest_vector() == [0.1, 0.2, 0.3]
    await fake_repo.save_profile_override({"ranking": {"alpha": 0.9}})
    override = await fake_repo.get_profile_override()
    assert override is not None and override["ranking"]["alpha"] == 0.9


@pytest.mark.asyncio
async def test_run_nightly_persists_interest_vector(
    monkeypatch: pytest.MonkeyPatch, fake_repo
) -> None:  # type: ignore[no-untyped-def]
    async def _get_repo():  # type: ignore[no-untyped-def]
        return fake_repo

    monkeypatch.setattr(pipeline, "get_repo", _get_repo)
    await pipeline.run_nightly()
    vec = await fake_repo.get_interest_vector()
    assert vec is not None and len(vec) == 1536


def test_tune_persists_profile_override(monkeypatch: pytest.MonkeyPatch, fake_repo) -> None:  # type: ignore[no-untyped-def]
    async def _get_repo():  # type: ignore[no-untyped-def]
        return fake_repo

    monkeypatch.setattr(api, "get_repo", _get_repo)
    client = TestClient(api.app)
    r = client.post("/api/tune", json={"instruction": "more systems papers, less agent drama"})
    assert r.status_code == 200
    # The steered profile was persisted (survives restart) ...
    assert fake_repo._app_state.get("profile_override") is not None
    # ... and the instruction was recorded as feedback for the audit trail.
    assert any(f.signal.value == "nl_instruction" for f in fake_repo._feedback)
