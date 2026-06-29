"""API security: signed email links, X-API-Key gate, rate limit, click shim."""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import aidigest.api.main as api  # noqa: E402
from aidigest.api import security  # noqa: E402
from aidigest.config import Settings  # noqa: E402
from aidigest.deliver.style import feedback_signature, feedback_url  # noqa: E402


def _settings(**kw: object) -> Settings:
    base: dict = {"AIDIGEST_LLM_MOCK": True}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


class _Repo:
    def __init__(self) -> None:
        self.feedback: list = []

    async def add_feedback(self, fb):  # type: ignore[no-untyped-def]
        stored = fb.model_copy(update={"id": str(len(self.feedback) + 1)})
        self.feedback.append(stored)
        return stored

    async def get_profile_override(self) -> dict | None:
        return None


# --------------------------------------------------------------------------- #
# Signing helpers
# --------------------------------------------------------------------------- #


def test_feedback_signature_stable_and_secret_dependent() -> None:
    a = feedback_signature(target_id="s1", target_kind="story", signal="up", value="1", secret="k")
    b = feedback_signature(target_id="s1", target_kind="story", signal="up", value="1", secret="k")
    c = feedback_signature(target_id="s1", target_kind="story", signal="up", value="1", secret="x")
    assert a == b and a != c and len(a) == 32


def test_feedback_signature_empty_secret_is_empty() -> None:
    assert feedback_signature(target_id="s", target_kind="story", signal="up", value="1", secret="") == ""


def test_feedback_url_signs_only_with_secret() -> None:
    unsigned = feedback_url("http://x", target_id="s", target_kind="story", signal="up")
    signed = feedback_url("http://x", target_id="s", target_kind="story", signal="up", secret="k")
    assert "sig=" not in unsigned
    assert "sig=" in signed


def test_verify_click_disabled_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "get_settings", _settings)
    assert security.verify_click_signature(
        target_id="s", target_kind="story", signal="up", value="1", sig=None
    )


def test_verify_click_enabled_requires_valid_sig(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _settings(AIDIGEST_LINK_SECRET="k")
    monkeypatch.setattr(security, "get_settings", lambda: cfg)
    good = feedback_signature(target_id="s", target_kind="story", signal="up", value="1", secret="k")
    assert security.verify_click_signature(
        target_id="s", target_kind="story", signal="up", value="1", sig=good
    )
    assert not security.verify_click_signature(
        target_id="s", target_kind="story", signal="up", value="1", sig="bad"
    )
    assert not security.verify_click_signature(
        target_id="s", target_kind="story", signal="up", value="1", sig=None
    )


# --------------------------------------------------------------------------- #
# Click shim route
# --------------------------------------------------------------------------- #


def test_feedback_click_records_and_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo()

    async def _get_repo() -> _Repo:
        return repo

    monkeypatch.setattr(api, "get_repo", _get_repo)
    client = TestClient(api.app)
    r = client.get(
        "/api/feedback/click",
        params={"target_id": "s1", "target_kind": "story", "signal": "up", "value": "1"},
    )
    assert r.status_code == 200
    assert "Recorded" in r.text
    assert repo.feedback and repo.feedback[0].target_id == "s1"


def test_feedback_click_rejects_bad_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _settings(AIDIGEST_LINK_SECRET="k")
    monkeypatch.setattr(security, "get_settings", lambda: cfg)
    r = TestClient(api.app).get(
        "/api/feedback/click",
        params={"target_id": "s1", "target_kind": "story", "signal": "up", "value": "1", "sig": "bad"},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Auth gate + rate limit
# --------------------------------------------------------------------------- #


def test_api_key_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo()

    async def _get_repo() -> _Repo:
        return repo

    monkeypatch.setattr(api, "get_repo", _get_repo)
    monkeypatch.setattr(security, "get_settings", lambda: _settings(AIDIGEST_API_KEY="secret"))
    client = TestClient(api.app)
    body = {"target_id": "s1", "target_kind": "story", "signal": "up", "value": 1.0}
    assert client.post("/api/feedback", json=body).status_code == 401
    ok = client.post("/api/feedback", json=body, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200


def test_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo()

    async def _get_repo() -> _Repo:
        return repo

    security._HITS.clear()
    monkeypatch.setattr(api, "get_repo", _get_repo)
    monkeypatch.setattr(security, "get_settings", lambda: _settings(AIDIGEST_RATE_LIMIT=2))
    client = TestClient(api.app)
    body = {"target_id": "s1", "target_kind": "story", "signal": "up", "value": 1.0}
    codes = [client.post("/api/feedback", json=body).status_code for _ in range(3)]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429
