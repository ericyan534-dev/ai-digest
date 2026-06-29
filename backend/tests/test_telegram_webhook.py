"""Telegram webhook: callback decoding + the /api/telegram/webhook feedback loop."""

from __future__ import annotations

import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import aidigest.api.main as api  # noqa: E402
from aidigest.config import Settings  # noqa: E402
from aidigest.deliver.telegram_bot import (  # noqa: E402
    decode_callback_data,
    extract_feedback_from_update,
)


def test_decode_callback_data_valid() -> None:
    assert decode_callback_data("fb:up:digest:daily-1") == ("up", "digest", "daily-1")
    assert decode_callback_data("fb:down:story:s9") == ("down", "story", "s9")


def test_decode_callback_data_invalid() -> None:
    assert decode_callback_data(None) is None
    assert decode_callback_data("nope") is None
    assert decode_callback_data("fb:sideways:digest:x") is None
    assert decode_callback_data("fb:up:digest:") is None


def test_extract_feedback_from_update() -> None:
    update = {"callback_query": {"id": "q1", "data": "fb:down:story:s1"}}
    assert extract_feedback_from_update(update) == ("down", "story", "s1", "q1")


def test_extract_feedback_ignores_plain_message() -> None:
    assert extract_feedback_from_update({"message": {"text": "hi"}}) is None


class _Repo:
    def __init__(self) -> None:
        self.feedback: list = []

    async def add_feedback(self, fb):  # type: ignore[no-untyped-def]
        self.feedback.append(fb)
        return fb.model_copy(update={"id": "1"})


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Repo]:
    repo = _Repo()

    async def _get_repo() -> _Repo:
        return repo

    monkeypatch.setattr(api, "get_repo", _get_repo)
    return TestClient(api.app), repo


def test_webhook_records_feedback(wired: tuple[TestClient, _Repo]) -> None:
    client, repo = wired
    r = client.post(
        "/api/telegram/webhook",
        json={"callback_query": {"id": "q1", "data": "fb:up:digest:d1"}},
    )
    assert r.status_code == 200 and r.json()["handled"] is True
    assert repo.feedback and repo.feedback[0].target_id == "d1"
    assert repo.feedback[0].signal.value == "up"


def test_webhook_ignores_non_feedback(wired: tuple[TestClient, _Repo]) -> None:
    client, _ = wired
    r = client.post("/api/telegram/webhook", json={"message": {"text": "hi"}})
    assert r.status_code == 200 and r.json()["handled"] is False


def test_webhook_secret_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: Settings(AIDIGEST_LLM_MOCK=True, TELEGRAM_WEBHOOK_SECRET="zzz"),  # type: ignore[call-arg]
    )
    client = TestClient(api.app)
    r = client.post(
        "/api/telegram/webhook",
        json={"callback_query": {"id": "q", "data": "fb:up:digest:d"}},
    )
    assert r.status_code == 403
