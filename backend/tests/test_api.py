"""API contract tests (FastAPI TestClient + a repo fake, mock LLM, no network).

Covers gate item (e): every API_CONTRACT.md route responds with the documented
shape. We inject a lightweight in-memory repo fake so the suite needs no
Postgres; `get_repo` is monkeypatched on the API module.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import aidigest.api.main as api
from aidigest.models import (
    DailyDigest,
    DigestKind,
    Family,
    Feedback,
    ImportanceTier,
    Story,
)

NOW = datetime(2026, 6, 21, 14, 3, 0, tzinfo=UTC)


class FakeRepo:
    """Minimal in-memory Repo standing in for the real psycopg-backed one."""

    def __init__(self) -> None:
        self._daily = DailyDigest(
            id="daily-2026-06-21",
            date="2026-06-21",
            tldr="Quiet day — nothing major shipped.",
            overall_tier=ImportanceTier.QUIET_DAY,
            quiet_day=True,
            story_ids=["deepseek-v4-ab12cd"],
            model="mock-flash",
            created_at=NOW,
        )
        self._story = Story(
            id="deepseek-v4-ab12cd",
            title="DeepSeek V4 released",
            family=Family.INDUSTRY,
            item_ids=["a"],
            representative_item_id="a",
            embedding=[0.1] * 1536,  # must be nulled over the wire
            importance=0.82,
            personal=0.74,
            final_rank=0.79,
            tier=ImportanceTier.BREAKTHROUGH,
            mention_count=7,
            created_at=NOW,
        )
        self._feedback: list[Feedback] = []

    async def list_digests(self, *, kind: DigestKind | None = None, limit: int = 30) -> list[dict]:
        row = {
            "id": self._daily.id,
            "kind": "daily",
            "date": self._daily.date,
            "tier": self._daily.overall_tier.value,
            "quiet": self._daily.quiet_day,
            "title": self._daily.tldr,
            "created_at": self._daily.created_at.isoformat(),
        }
        if kind is not None and kind != DigestKind.DAILY:
            return []
        return [row][:limit]

    async def get_digest(self, digest_id: str) -> DailyDigest | None:
        return self._daily if digest_id == self._daily.id else None

    async def get_stories_for_date(self, date: str) -> list[Story]:
        return [self._story]

    async def add_feedback(self, fb: Feedback) -> Feedback:
        stored = fb.model_copy(update={"id": str(len(self._feedback) + 1)})
        self._feedback.append(stored)
        return stored

    async def get_profile_override(self) -> dict | None:
        return None

    async def save_profile_override(self, profile: dict) -> None:
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake = FakeRepo()

    async def _fake_get_repo() -> FakeRepo:
        return fake

    monkeypatch.setattr(api, "get_repo", _fake_get_repo)
    return TestClient(api.app)


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["llm_mock"] is True
    assert body["version"]


def test_list_digests(client: TestClient) -> None:
    r = client.get("/api/digests")
    assert r.status_code == 200
    rows = r.json()
    assert rows and rows[0]["id"] == "daily-2026-06-21"
    assert rows[0]["title"] == "Quiet day — nothing major shipped."


def test_list_digests_kind_filter(client: TestClient) -> None:
    assert client.get("/api/digests?kind=daily").json()
    assert client.get("/api/digests?kind=weekly").json() == []


def test_list_digests_limit_validation(client: TestClient) -> None:
    assert client.get("/api/digests?limit=0").status_code == 422
    assert client.get("/api/digests?limit=101").status_code == 422


def test_get_digest_found(client: TestClient) -> None:
    r = client.get("/api/digest/daily-2026-06-21")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "daily"
    assert body["quiet_day"] is True


def test_get_digest_missing(client: TestClient) -> None:
    r = client.get("/api/digest/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "digest not found"}


def test_stories_nulls_embedding(client: TestClient) -> None:
    r = client.get("/api/stories?date=2026-06-21")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["id"] == "deepseek-v4-ab12cd"
    assert rows[0]["embedding"] is None  # never ship 1536 floats


def test_post_feedback(client: TestClient) -> None:
    r = client.post(
        "/api/feedback",
        json={"target_id": "deepseek-v4-ab12cd", "target_kind": "story", "signal": "up", "value": 1.0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == "1"


def test_post_feedback_validation(client: TestClient) -> None:
    r = client.post("/api/feedback", json={"target_kind": "story", "signal": "up"})
    assert r.status_code == 422


def test_post_tune(client: TestClient) -> None:
    r = client.post("/api/tune", json={"instruction": "more kernel/systems papers"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["profile"], dict)


def test_post_tune_validation(client: TestClient) -> None:
    assert client.post("/api/tune", json={}).status_code == 422
    assert client.post("/api/tune", json={"instruction": ""}).status_code == 422


def test_health_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom() -> FakeRepo:
        raise RuntimeError("no postgres")

    monkeypatch.setattr(api, "get_repo", _boom)
    r = TestClient(api.app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["db"] == "down"


# --------------------------------------------------------------------------- #
# Feedback round-trip + weekly detail, wired through the shared conftest
# FakeRepo (full repo surface with real persistence semantics).
# --------------------------------------------------------------------------- #


@pytest.fixture
def shared_client(monkeypatch: pytest.MonkeyPatch, fake_repo, busy_daily, sample_weekly):
    """A TestClient backed by the conftest FakeRepo, pre-seeded with one daily
    and one weekly digest so list/detail/feedback all exercise real persistence.
    """
    import asyncio

    async def _seed() -> None:
        await fake_repo.save_daily(busy_daily)
        await fake_repo.save_weekly(sample_weekly)

    asyncio.run(_seed())

    async def _get_repo():
        return fake_repo

    monkeypatch.setattr(api, "get_repo", _get_repo)
    return TestClient(api.app), fake_repo


def test_feedback_round_trip_persists(shared_client) -> None:
    import asyncio

    client, repo = shared_client
    r = client.post(
        "/api/feedback",
        json={"target_id": "deepseek-v4-ab12cd", "target_kind": "story",
              "signal": "up", "value": 1.0},
    )
    assert r.status_code == 200
    fb_id = r.json()["id"]
    # The feedback is actually stored and readable back from the repo.
    stored = asyncio.run(repo.get_feedback())
    assert any(str(f.id) == str(fb_id) for f in stored)
    assert stored[-1].target_id == "deepseek-v4-ab12cd"
    assert stored[-1].signal.value == "up"


def test_list_and_get_weekly_detail(shared_client) -> None:
    client, _ = shared_client
    rows = client.get("/api/digests?kind=weekly").json()
    assert rows and rows[0]["kind"] == "weekly"
    weekly_id = rows[0]["id"]
    body = client.get(f"/api/digest/{weekly_id}").json()
    assert body["kind"] == "weekly"
    assert body["title"]
    assert body["candidate_count"] == 3
    assert "shortlist" in body and "on_my_radar" in body


def test_list_both_kinds(shared_client) -> None:
    client, _ = shared_client
    rows = client.get("/api/digests").json()
    kinds = {r["kind"] for r in rows}
    assert kinds == {"daily", "weekly"}
