"""Tests for Repo.connect() transient-failure retry (see `aidigest/db/repo.py`).

Bootstrapping a connection touches the network twice — the raw connection in
`_ensure_vector_extension` and the pool's own `open(wait=True)` — either of
which can hit a transient timeout against a remote managed Postgres (e.g.
Supabase). Mock at the `psycopg.AsyncConnection.connect` / `AsyncConnectionPool`
boundary (mirroring tests/test_db_rows.py's offline-mocking style) so none of
these tests need a real database.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("AIDIGEST_LLM_MOCK", "1")

from typing import Any
from unittest.mock import AsyncMock

import psycopg
import psycopg_pool
import pytest

from aidigest.db.repo import Repo

FAKE_DSN = "postgresql://u:SUPERSECRET@h/db"


class _FakeConn:
    """Stand-in for the raw bootstrap connection in _ensure_vector_extension."""

    def __init__(self) -> None:
        self.execute = AsyncMock()
        self.close = AsyncMock()


def _make_fake_pool_class(open_queue: list[BaseException | None]) -> type:
    """Build a psycopg_pool.AsyncConnectionPool stand-in.

    Each new instance (one per connect() attempt) consumes one entry from
    `open_queue` on `.open()`: `None` succeeds, an exception instance is raised.
    Once the queue is empty, `.open()` always succeeds.
    """

    class _FakePool:
        instances: list[_FakePool] = []

        def __init__(
            self,
            dsn: str,
            *,
            min_size: int,
            max_size: int,
            configure: Any,
            open: bool,
        ) -> None:
            self.dsn = dsn
            self.configure = configure
            self.closed = False
            self._open_effect = open_queue.pop(0) if open_queue else None
            _FakePool.instances.append(self)

        async def open(self, wait: bool = True) -> None:
            if self._open_effect is not None:
                raise self._open_effect

        async def close(self) -> None:
            self.closed = True

    return _FakePool


async def test_connect_succeeds_on_second_attempt_after_transient_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _make_fake_pool_class([]))
    connect_mock = AsyncMock(
        side_effect=[psycopg.errors.ConnectionTimeout("connection timeout expired"), _FakeConn()]
    )
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect_mock)

    repo = Repo(dsn=FAKE_DSN)
    await repo.connect()

    assert connect_mock.await_count == 2
    assert repo._pool is not None


async def test_connect_gives_up_after_five_attempts_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _make_fake_pool_class([]))
    connect_mock = AsyncMock(side_effect=psycopg.errors.ConnectionTimeout("timeout"))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect_mock)

    repo = Repo(dsn=FAKE_DSN)
    with pytest.raises(psycopg.errors.ConnectionTimeout):
        await repo.connect()

    assert connect_mock.await_count == 5
    assert repo._pool is None


async def test_connect_does_not_retry_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _make_fake_pool_class([]))
    connect_mock = AsyncMock(side_effect=psycopg.ProgrammingError("bad dsn"))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect_mock)

    repo = Repo(dsn=FAKE_DSN)
    with pytest.raises(psycopg.ProgrammingError):
        await repo.connect()

    assert connect_mock.await_count == 1
    assert repo._pool is None


async def test_connect_leaves_pool_none_after_failure_so_retry_can_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _make_fake_pool_class([]))
    connect_mock = AsyncMock(side_effect=psycopg.errors.ConnectionTimeout("timeout"))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect_mock)

    repo = Repo(dsn=FAKE_DSN)
    with pytest.raises(psycopg.errors.ConnectionTimeout):
        await repo.connect()
    assert repo._pool is None

    # No leaked half-open pool: a later call must be free to try again and succeed.
    connect_mock.side_effect = None
    connect_mock.return_value = _FakeConn()
    await repo.connect()
    assert repo._pool is not None


async def test_connect_retries_pool_open_transient_failure_and_closes_failed_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_mock = AsyncMock(return_value=_FakeConn())
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect_mock)

    fake_pool_cls = _make_fake_pool_class([psycopg.OperationalError("pool open timeout")])
    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", fake_pool_cls)

    repo = Repo(dsn=FAKE_DSN)
    await repo.connect()

    assert len(fake_pool_cls.instances) == 2
    assert fake_pool_cls.instances[0].closed is True  # failed pool cleaned up, not leaked
    assert repo._pool is fake_pool_cls.instances[1]


async def test_connect_is_a_noop_when_already_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repo(dsn=FAKE_DSN)
    sentinel = object()
    repo._pool = sentinel  # type: ignore[assignment]

    # Deliberately do NOT patch psycopg/psycopg_pool: a real import would blow
    # up if connect() ever tried to use them here.
    await repo.connect()

    assert repo._pool is sentinel


async def test_connect_retry_logs_never_leak_the_dsn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _make_fake_pool_class([]))
    connect_mock = AsyncMock(side_effect=psycopg.errors.ConnectionTimeout("timeout"))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect_mock)

    repo = Repo(dsn=FAKE_DSN)
    caplog.set_level(logging.WARNING)
    with pytest.raises(psycopg.errors.ConnectionTimeout):
        await repo.connect()

    assert "SUPERSECRET" not in caplog.text
    assert FAKE_DSN not in caplog.text
    # Sanity: retries actually happened and were logged (not a vacuous pass).
    assert any("ConnectionTimeout" in r.message for r in caplog.records)
