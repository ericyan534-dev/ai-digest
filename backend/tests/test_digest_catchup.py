"""Catch-up + delivery bookkeeping tests (see aidigest/flows/pipeline.py's
`run_daily_if_missing` / `run_weekly_if_missing` and `aidigest/db/repo.py`'s
delivery/claim methods).

Root incident: GitHub Actions runs 31118610124 / 31120872463 (2026-08-06) both
failed with "job was not acquired by Runner" — the daily digest job never
started, and nothing retried or alerted. These tests cover the self-heal
(catch-up) and the audibility (claim/delivery bookkeeping + the workflow's
alert-on-failure wiring) fixes for that gap.

Reuses tests/test_pipeline.py's `FakeRepo` + `wired` fixture (same offline,
MOCK-LLM, no-network harness) rather than inventing a new one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import aidigest.flows.pipeline as pipeline
from aidigest.db.repo import Repo
from aidigest.models import DailyDigest, DigestKind, Item, WeeklyDigest
from tests.test_pipeline import FakeRepo, _items  # reuse the established fake/harness

DATE = "2026-06-21"  # matches conftest's `busy_daily` fixture (id="daily-2026-06-21")
WEEK_OF = "2026-06-15"  # matches conftest's `sample_weekly` fixture (week_of="2026-06-15")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "digest.yml"


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    """Same wiring as tests/test_pipeline.py's `wired` fixture (offline, MOCK LLM,
    zero network), redefined locally rather than imported: pytest fixture lookup
    is name-based, and importing a same-named fixture across modules trips
    ruff/pyflakes F811 on every test function parameter named `wired`.
    """
    repo = FakeRepo()

    async def _get_repo() -> FakeRepo:
        return repo

    async def _ingest_all(since: datetime, *, adapters: object = None) -> list[Item]:
        return _items()

    monkeypatch.setattr(pipeline, "get_repo", _get_repo)
    monkeypatch.setattr("aidigest.ingest.registry.ingest_all", _ingest_all)
    return repo


# --------------------------------------------------------------------------- #
# Repo: record_delivery / get_delivery / claim_digest_run / get_claim
# --------------------------------------------------------------------------- #
# Pure-logic coverage. The real Repo's app_state SQL (save_app_state /
# get_app_state) is only exercised against a real Postgres, which is out of
# scope here (see tests/test_repo_attach_items.py / test_repo_connect_retry.py
# for the house style of stubbing at that boundary rather than hitting a DB).
#
# Repo.try_claim_digest_run and Repo.claim_digest_run are NOT covered here for
# the same reason, but more so: both are hand-written raw SQL (INSERT ... ON
# CONFLICT DO UPDATE ..., the former additionally gated by a WHERE clause and
# RETURNING key) that stamps `at` with Postgres `now()` — deliberately bypassing
# save_app_state/get_app_state entirely so the write and the staleness reads
# share exactly one clock. A Python-side stub of save_app_state/get_app_state
# cannot exercise either statement at all, and a fake that reimplements the SQL
# in Python would only prove itself, not the SQL. The pipeline-level race
# regression below (test_two_concurrent_catchups_run_the_digest_exactly_once)
# covers the CALL PATTERN (_catch_up must gate on try_claim_digest_run's return
# value); the SQL itself needs review by eye and, ideally, a smoke test against
# a real Postgres before ship. get_claim's own logic (key naming + read-through
# to get_app_state) is still covered below by seeding the stub directly rather
# than going through claim_digest_run.


class _AppStateStub:
    """Minimal in-memory stand-in for Repo.save_app_state / get_app_state."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def save_app_state(self, key: str, value: dict) -> None:
        self.store[key] = dict(value)

    async def get_app_state(self, key: str) -> dict | None:
        return self.store.get(key)


def _repo_with_stubbed_app_state() -> Repo:
    repo = Repo(dsn="postgresql://u:p@h/db")
    stub = _AppStateStub()
    repo.save_app_state = stub.save_app_state  # type: ignore[method-assign]
    repo.get_app_state = stub.get_app_state  # type: ignore[method-assign]
    return repo


@pytest.mark.asyncio
async def test_record_delivery_and_get_delivery_round_trip() -> None:
    repo = _repo_with_stubbed_app_state()
    assert await repo.get_delivery(DigestKind.DAILY) is None

    await repo.record_delivery(DigestKind.DAILY, DATE, email=True, telegram=False, delivered=True)

    delivery = await repo.get_delivery(DigestKind.DAILY)
    assert delivery is not None
    assert delivery["date"] == DATE
    assert delivery["email"] is True
    assert delivery["telegram"] is False
    assert delivery["delivered"] is True
    assert delivery["at"]  # a UTC isoformat timestamp was recorded

    # weekly lives under a DIFFERENT key — no cross-kind bleed.
    assert await repo.get_delivery(DigestKind.WEEKLY) is None


@pytest.mark.asyncio
async def test_get_claim_reads_through_by_kind_key() -> None:
    """get_claim's own logic: reads through to get_app_state under the correct
    per-kind key, and does not bleed across kinds. Seeds the stub's store
    directly (rather than via claim_digest_run, which is raw SQL — see the
    comment block above) since only get_claim's read-through is testable here.
    """
    repo = Repo(dsn="postgresql://u:p@h/db")
    stub = _AppStateStub()
    repo.save_app_state = stub.save_app_state  # type: ignore[method-assign]
    repo.get_app_state = stub.get_app_state  # type: ignore[method-assign]

    assert await repo.get_claim(DigestKind.WEEKLY) is None

    stub.store[f"claim_{DigestKind.WEEKLY.value}"] = {"date": WEEK_OF, "at": "2026-06-15T00:00:00+00:00"}

    claim = await repo.get_claim(DigestKind.WEEKLY)
    assert claim is not None
    assert claim["date"] == WEEK_OF
    assert claim["at"]

    assert await repo.get_claim(DigestKind.DAILY) is None


# --------------------------------------------------------------------------- #
# run_daily_if_missing — already-delivered / in-flight / proceed matrix
# --------------------------------------------------------------------------- #
# These seed the FakeRepo directly (via a stored fixture digest) rather than
# running the real pipeline, so the claim `run_daily` itself writes never
# confounds the in-flight check under test.


@pytest.mark.asyncio
async def test_run_daily_if_missing_skips_and_returns_stored_when_already_delivered(
    wired: FakeRepo, busy_daily: DailyDigest
) -> None:
    await wired.save_daily(busy_daily)
    await wired.record_delivery(DigestKind.DAILY, DATE, email=True, telegram=False, delivered=True)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert result is not None
    assert result.id == busy_daily.id


@pytest.mark.asyncio
async def test_run_daily_if_missing_returns_stored_when_deliver_false_regardless_of_delivery(
    wired: FakeRepo, busy_daily: DailyDigest
) -> None:
    """`deliver=False` means delivery was never asked for, so a stored digest
    alone (no delivery record at all) already counts as done."""
    await wired.save_daily(busy_daily)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=False)

    assert result is not None
    assert result.id == busy_daily.id


@pytest.mark.asyncio
async def test_run_daily_if_missing_proceeds_when_digest_exists_but_not_delivered(
    wired: FakeRepo, busy_daily: DailyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The partial-failure case: generated but never emailed."""
    await wired.save_daily(busy_daily)  # generated ... but no delivery record, no claim

    called: dict[str, object] = {}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        called["date"] = date
        called["deliver"] = deliver
        return busy_daily

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert called == {"date": DATE, "deliver": True}
    assert result is busy_daily


@pytest.mark.asyncio
async def test_run_daily_if_missing_proceeds_when_delivery_recorded_for_a_different_date(
    wired: FakeRepo, busy_daily: DailyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    await wired.save_daily(busy_daily)
    await wired.record_delivery(
        DigestKind.DAILY, "2026-06-20", email=True, telegram=False, delivered=True
    )

    called = {"n": 0}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        called["n"] += 1
        return busy_daily

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert called["n"] == 1
    assert result is busy_daily


@pytest.mark.asyncio
async def test_run_daily_if_missing_proceeds_when_nothing_exists(
    wired: FakeRepo, busy_daily: DailyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incident case: no digest, no delivery record, no claim at all."""
    called = {"n": 0}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        called["n"] += 1
        return busy_daily

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert called["n"] == 1
    assert result is busy_daily


@pytest.mark.asyncio
async def test_run_daily_if_missing_regenerates_when_stored_digest_type_mismatches_kind(
    wired: FakeRepo,
    sample_weekly: WeeklyDigest,
    busy_daily: DailyDigest,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Defends the isinstance-guarded cast in `_catch_up`: if the object found
    under `digest_id` is NOT the type `kind` expects (a data-integrity anomaly —
    e.g. the digests table's `kind` column and its JSON content diverging), it
    must NOT be silently returned to the caller. An unchecked cast would hand a
    WeeklyDigest to code that expects a DailyDigest (e.g. render_daily_md),
    crashing somewhere far less diagnosable than here. Instead: log loudly and
    fall through to regenerate — the self-healing behavior this feature exists
    for in the first place.
    """
    wired.dailies["daily-2026-06-21"] = sample_weekly  # type: ignore[assignment]

    called = {"n": 0}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        called["n"] += 1
        return busy_daily

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    # deliver=False so "already done" turns on `stored is not None` alone (see
    # _catch_up) — the branch that reaches the (now isinstance-guarded) cast.
    with caplog.at_level(logging.ERROR, logger="aidigest.flows"):
        result = await pipeline.run_daily_if_missing(date=DATE, deliver=False)

    assert called["n"] == 1, "must regenerate rather than trust the mismatched stored object"
    assert result is busy_daily
    assert any(
        r.levelname == "ERROR" and "type_mismatch" in r.message for r in caplog.records
    ), "the type mismatch must be logged loudly, not silently swallowed"


@pytest.mark.asyncio
async def test_run_daily_if_missing_skips_with_none_when_claim_is_fresh(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    await wired.claim_digest_run(DigestKind.DAILY, DATE)

    called = {"n": 0}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        called["n"] += 1
        raise AssertionError("must not run while a fresh claim is in flight")

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert result is None
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_run_daily_if_missing_proceeds_when_claim_is_stale(
    wired: FakeRepo, busy_daily: DailyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_at = (datetime.now(UTC) - pipeline._CLAIM_TTL - timedelta(minutes=1)).isoformat()
    await wired.save_app_state(f"claim_{DigestKind.DAILY.value}", {"date": DATE, "at": stale_at})

    called = {"n": 0}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        called["n"] += 1
        return busy_daily

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    result = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert called["n"] == 1
    assert result is busy_daily


@pytest.mark.asyncio
async def test_two_sequential_run_daily_if_missing_calls_produce_exactly_one_run(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """Functional check, NOT the race regression guard: two purely SEQUENTIAL
    catch-up calls (the second only starts after the first has fully returned)
    correctly produce one run and one in_flight skip. This passes under a naive
    read-then-write claim design too — see
    test_two_concurrent_catchups_run_the_digest_exactly_once below for the test
    that actually discriminates the atomic-CAS fix from that design.

    The first call uses deliver=False so it runs to completion (via the real
    run_daily) WITHOUT ever recording a delivery — modeling "no delivery
    recorded in between" the two calls. Its own unconditional claim_digest_run
    leaves a fresh claim behind. The second call (deliver=True, so its own
    "already done" check can't short-circuit on the digest existing alone) must
    then lose the claim race and back off with reason=in_flight.
    """
    await pipeline.run_ingest()

    first = await pipeline.run_daily_if_missing(date=DATE, deliver=False)
    assert first is not None  # the first call actually ran and returned a digest
    assert await wired.get_delivery(DigestKind.DAILY) is None  # never delivered

    with caplog.at_level(logging.INFO, logger="aidigest.flows"):
        second = await pipeline.run_daily_if_missing(date=DATE, deliver=True)

    assert second is None  # the second call lost the claim race
    assert any(
        "status=skip reason=in_flight" in r.message for r in caplog.records
    ), "second call must log the in_flight skip, not silently do nothing"


@pytest.mark.asyncio
async def test_two_concurrent_catchups_run_the_digest_exactly_once(
    wired: FakeRepo, busy_daily: DailyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE discriminating regression guard for the 2026-08-06-class race: a
    delayed primary and an on-time catch-up slot starting within seconds of each
    other (GitHub's routine 1-2.5h scheduling delay makes a ~3h gap land exactly
    like this) must not both deliver.

    Unlike a purely sequential two-call test (which would ALSO pass under the
    OLD read-then-write claim design, since the first call's claim-write always
    happens-before the second call even starts), this drives both catch-up
    calls through `asyncio.gather` with a fake `run_daily` that yields control
    (`await asyncio.sleep(0)`) between being invoked and returning. That forces
    the two coroutines to genuinely interleave right around the claim decision,
    the way two independent GitHub Actions runners racing in real time would.
    Only `_catch_up`'s atomic `repo.try_claim_digest_run` compare-and-set can
    make exactly one of them win; a read-then-write design would let both
    coroutines observe "no fresh claim" during that interleaved window and both
    proceed, double-sending the digest.
    """
    calls = {"n": 0}

    async def _fake_run_daily(
        *, date: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> DailyDigest:
        calls["n"] += 1
        await asyncio.sleep(0)  # yield control so the other gathered call can run
        return busy_daily

    monkeypatch.setattr(pipeline, "run_daily", _fake_run_daily)

    results = await asyncio.gather(
        pipeline.run_daily_if_missing(date=DATE, deliver=True),
        pipeline.run_daily_if_missing(date=DATE, deliver=True),
    )

    assert calls["n"] == 1, "the digest must be generated exactly once"
    assert sum(r is None for r in results) == 1, "exactly one caller must lose the claim race"
    assert sum(r is busy_daily for r in results) == 1, "the winner must return the real digest"


# --------------------------------------------------------------------------- #
# run_weekly_if_missing — same matrix, weekly-shaped
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_weekly_if_missing_skips_and_returns_stored_when_already_delivered(
    wired: FakeRepo, sample_weekly: WeeklyDigest
) -> None:
    await wired.save_weekly(sample_weekly)
    await wired.record_delivery(
        DigestKind.WEEKLY, WEEK_OF, email=True, telegram=False, delivered=True
    )

    result = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True)

    assert result is not None
    assert result.id == sample_weekly.id


@pytest.mark.asyncio
async def test_run_weekly_if_missing_proceeds_when_digest_exists_but_not_delivered(
    wired: FakeRepo, sample_weekly: WeeklyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    await wired.save_weekly(sample_weekly)

    called = {"n": 0}

    async def _fake_run_weekly(
        *, week_of: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> WeeklyDigest:
        called["n"] += 1
        return sample_weekly

    monkeypatch.setattr(pipeline, "run_weekly", _fake_run_weekly)

    result = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True)

    assert called["n"] == 1
    assert result is sample_weekly


@pytest.mark.asyncio
async def test_run_weekly_if_missing_proceeds_when_nothing_exists(
    wired: FakeRepo, sample_weekly: WeeklyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"n": 0}

    async def _fake_run_weekly(
        *, week_of: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> WeeklyDigest:
        called["n"] += 1
        return sample_weekly

    monkeypatch.setattr(pipeline, "run_weekly", _fake_run_weekly)

    result = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True)

    assert called["n"] == 1
    assert result is sample_weekly


@pytest.mark.asyncio
async def test_run_weekly_if_missing_skips_with_none_when_claim_is_fresh(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    await wired.claim_digest_run(DigestKind.WEEKLY, WEEK_OF)

    called = {"n": 0}

    async def _fake_run_weekly(
        *, week_of: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> WeeklyDigest:
        called["n"] += 1
        raise AssertionError("must not run while a fresh claim is in flight")

    monkeypatch.setattr(pipeline, "run_weekly", _fake_run_weekly)

    result = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True)

    assert result is None
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_run_weekly_if_missing_proceeds_when_claim_is_stale(
    wired: FakeRepo, sample_weekly: WeeklyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_at = (datetime.now(UTC) - pipeline._CLAIM_TTL - timedelta(minutes=1)).isoformat()
    await wired.save_app_state(
        f"claim_{DigestKind.WEEKLY.value}", {"date": WEEK_OF, "at": stale_at}
    )

    called = {"n": 0}

    async def _fake_run_weekly(
        *, week_of: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> WeeklyDigest:
        called["n"] += 1
        return sample_weekly

    monkeypatch.setattr(pipeline, "run_weekly", _fake_run_weekly)

    result = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True)

    assert called["n"] == 1
    assert result is sample_weekly


@pytest.mark.asyncio
async def test_two_sequential_run_weekly_if_missing_calls_produce_exactly_one_run(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """Weekly counterpart of the daily SEQUENTIAL functional check above — see
    test_two_concurrent_weekly_catchups_run_the_digest_exactly_once below for
    the test that actually discriminates the atomic-CAS fix."""
    await pipeline.run_ingest()
    await pipeline.run_process()

    first = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=False)
    assert first is not None
    assert await wired.get_delivery(DigestKind.WEEKLY) is None  # never delivered

    with caplog.at_level(logging.INFO, logger="aidigest.flows"):
        second = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True)

    assert second is None  # the second call lost the claim race
    assert any(
        "status=skip reason=in_flight" in r.message for r in caplog.records
    ), "second call must log the in_flight skip, not silently do nothing"


@pytest.mark.asyncio
async def test_two_concurrent_weekly_catchups_run_the_digest_exactly_once(
    wired: FakeRepo, sample_weekly: WeeklyDigest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weekly counterpart of test_two_concurrent_catchups_run_the_digest_exactly_once
    above — see that test's docstring for why this is the discriminating regression
    guard (a purely sequential test is not)."""
    calls = {"n": 0}

    async def _fake_run_weekly(
        *, week_of: str | None = None, deliver: bool = False, **_kwargs: object
    ) -> WeeklyDigest:
        calls["n"] += 1
        await asyncio.sleep(0)  # yield control so the other gathered call can run
        return sample_weekly

    monkeypatch.setattr(pipeline, "run_weekly", _fake_run_weekly)

    results = await asyncio.gather(
        pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True),
        pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=True),
    )

    assert calls["n"] == 1, "the digest must be generated exactly once"
    assert sum(r is None for r in results) == 1, "exactly one caller must lose the claim race"
    assert sum(r is sample_weekly for r in results) == 1, "the winner must return the real digest"


# --------------------------------------------------------------------------- #
# run_daily / run_weekly: claim-before-generate + record-delivery-after-deliver
# --------------------------------------------------------------------------- #
# These drive the REAL pipeline end to end (mock LLM, FakeRepo) to prove the
# side effects actually happen, not just the catch-up logic that reads them.


def _settings_with_channels(*, email: bool, telegram: bool):
    from aidigest.config import get_settings as _real_get_settings

    update: dict[str, object] = {"wiki_dir": ""}
    update["resend_api_key"] = "k" if email else ""
    update["digest_from_email"] = "a@b.com" if email else ""
    update["digest_to_email"] = "c@d.com" if email else ""
    update["telegram_bot_token"] = "t" if telegram else ""
    update["telegram_chat_id"] = "1" if telegram else ""
    return _real_get_settings().model_copy(update=update)


@pytest.mark.asyncio
async def test_run_daily_writes_a_claim_before_generating(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_claim: dict | None = None
    real_generate_daily = pipeline.generate_daily

    async def _spy_generate_daily(*args: object, **kwargs: object) -> DailyDigest:
        nonlocal seen_claim
        seen_claim = await wired.get_claim(DigestKind.DAILY)
        return await real_generate_daily(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "generate_daily", _spy_generate_daily)

    assert await wired.get_claim(DigestKind.DAILY) is None
    await pipeline.run_ingest()
    await pipeline.run_daily(date=DATE)

    assert seen_claim is not None, "generate_daily ran before a claim was written"
    assert seen_claim["date"] == DATE


@pytest.mark.asyncio
async def test_run_daily_warns_when_overriding_a_fresh_in_flight_claim(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """The manual force path (`run_daily` called directly, unconditionally) must
    stay unconditional — gating it would defeat the point of a force/override
    escape hatch — but it must not be SILENT about clobbering a claim another
    run may still be actively holding. A fresh claim for the SAME date logs a
    WARNING; the run still proceeds (see the no-warning cases below for the
    non-colliding claim states).
    """
    await wired.claim_digest_run(DigestKind.DAILY, DATE)

    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        digest = await pipeline.run_daily(date=DATE)

    assert digest is not None  # force path still ran to completion
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "in_flight" in r.message and DATE in r.message for r in warnings
    ), f"expected an in-flight claim override warning, got: {[r.message for r in warnings]}"


@pytest.mark.asyncio
async def test_run_daily_does_not_warn_when_no_claim_exists(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_daily(date=DATE)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
async def test_run_daily_does_not_warn_when_claim_is_stale(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    stale_at = (datetime.now(UTC) - pipeline._CLAIM_TTL - timedelta(minutes=1)).isoformat()
    await wired.save_app_state(f"claim_{DigestKind.DAILY.value}", {"date": DATE, "at": stale_at})

    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_daily(date=DATE)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
async def test_run_daily_does_not_warn_when_claim_is_for_a_different_date(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    await wired.claim_digest_run(DigestKind.DAILY, "2026-06-20")

    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_daily(date=DATE)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
async def test_run_daily_if_missing_does_not_warn_about_its_own_just_won_claim(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """`_catch_up` atomically wins the claim via `try_claim_digest_run`, then
    calls the real `run_daily`, which immediately re-reads the SAME claim it
    (transitively) just won a moment earlier. That must NOT be reported as an
    override of another run's in-flight claim — it is this exact call chain
    refreshing its own claim, not a collision. If the in-flight-claim warning
    fired here, EVERY successful catch-up run would log a false "may double-
    deliver" alarm, which is worse than no warning at all.
    """
    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        digest = await pipeline.run_daily_if_missing(date=DATE, deliver=False)

    assert digest is not None
    assert not [
        r for r in caplog.records if r.levelname == "WARNING"
    ], "run_daily_if_missing must never warn about the claim it just atomically won"


@pytest.mark.asyncio
async def test_run_daily_records_delivery_after_delivering_both_channels_ok(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        assert await wired.get_delivery(DigestKind.DAILY) is None  # not recorded yet
        return True

    async def _fake_tg_send_daily(digest: DailyDigest) -> bool:
        return True

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: _settings_with_channels(email=True, telegram=True)
    )
    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)
    monkeypatch.setattr(pipeline, "tg_send_daily", _fake_tg_send_daily)

    await pipeline.run_ingest()
    await pipeline.run_daily(date=DATE, deliver=True)

    delivery = await wired.get_delivery(DigestKind.DAILY)
    assert delivery is not None
    assert delivery["date"] == DATE
    assert delivery["email"] is True
    assert delivery["telegram"] is True
    assert delivery["delivered"] is True


@pytest.mark.asyncio
async def test_run_daily_records_delivery_before_wiki_export(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the crash-window shrink: record_delivery must run
    BEFORE the wiki export, not after — wiki export is unrelated filesystem work
    that only widens the window in which a crash could lose a successful send's
    delivery record (see _delivery_matches for why that lost-record case is an
    accepted at-least-once tradeoff, not a reason to widen the window further).
    """
    settings_with_wiki = _settings_with_channels(email=True, telegram=True).model_copy(
        update={"wiki_dir": "/tmp/unused-wiki-dir-for-this-test"}
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings_with_wiki)

    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        return True

    async def _fake_tg_send_daily(digest: DailyDigest) -> bool:
        return True

    seen: dict[str, object] = {}

    def _spy_wiki_export_daily(digest: DailyDigest, *, wiki_dir: str) -> list:
        seen["called"] = True
        seen["delivery_at_wiki_time"] = wired.state.get(f"delivery_{DigestKind.DAILY.value}")
        return []

    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)
    monkeypatch.setattr(pipeline, "tg_send_daily", _fake_tg_send_daily)
    monkeypatch.setattr(pipeline, "wiki_export_daily", _spy_wiki_export_daily)

    await pipeline.run_ingest()
    await pipeline.run_daily(date=DATE, deliver=True)

    assert seen.get("called") is True, "the wiki export spy never ran"
    assert seen.get("delivery_at_wiki_time") is not None, (
        "record_delivery must be awaited before wiki export runs"
    )


@pytest.mark.asyncio
async def test_run_daily_records_delivery_flags_email_only(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        return True

    async def _fake_tg_send_daily(digest: DailyDigest) -> bool:
        return False  # unconfigured -> self-disabled

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: _settings_with_channels(email=True, telegram=False)
    )
    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)
    monkeypatch.setattr(pipeline, "tg_send_daily", _fake_tg_send_daily)

    await pipeline.run_ingest()
    await pipeline.run_daily(date=DATE, deliver=True)

    delivery = await wired.get_delivery(DigestKind.DAILY)
    assert delivery is not None
    assert delivery["email"] is True
    assert delivery["telegram"] is False
    assert delivery["delivered"] is True


@pytest.mark.asyncio
async def test_run_daily_records_delivered_true_and_logs_channels_none_when_neither_configured(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        return False  # self-disabled: not configured

    async def _fake_tg_send_daily(digest: DailyDigest) -> bool:
        return False  # self-disabled: not configured

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: _settings_with_channels(email=False, telegram=False)
    )
    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)
    monkeypatch.setattr(pipeline, "tg_send_daily", _fake_tg_send_daily)

    await pipeline.run_ingest()
    with caplog.at_level(logging.INFO, logger="aidigest.flows"):
        await pipeline.run_daily(date=DATE, deliver=True)

    delivery = await wired.get_delivery(DigestKind.DAILY)
    assert delivery is not None
    assert delivery["email"] is False
    assert delivery["telegram"] is False
    assert delivery["delivered"] is True  # nothing to deliver != a failed delivery

    ok_lines = [
        r.message for r in caplog.records if r.message.startswith("step=deliver_daily status=ok")
    ]
    assert ok_lines, "no completion log line for the deliver_daily step"
    assert "channels=none" in ok_lines[-1]


@pytest.mark.asyncio
async def test_run_weekly_writes_a_claim_before_generating(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_claim: dict | None = None
    real_generate_weekly = pipeline.generate_weekly

    async def _spy_generate_weekly(*args: object, **kwargs: object) -> WeeklyDigest:
        nonlocal seen_claim
        seen_claim = await wired.get_claim(DigestKind.WEEKLY)
        return await real_generate_weekly(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "generate_weekly", _spy_generate_weekly)

    assert await wired.get_claim(DigestKind.WEEKLY) is None
    await pipeline.run_ingest()
    await pipeline.run_process()
    await pipeline.run_weekly(week_of=WEEK_OF)

    assert seen_claim is not None, "generate_weekly ran before a claim was written"
    assert seen_claim["date"] == WEEK_OF


@pytest.mark.asyncio
async def test_run_weekly_warns_when_overriding_a_fresh_in_flight_claim(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """Weekly counterpart of test_run_daily_warns_when_overriding_a_fresh_in_flight_claim."""
    await wired.claim_digest_run(DigestKind.WEEKLY, WEEK_OF)

    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_process()
        digest = await pipeline.run_weekly(week_of=WEEK_OF)

    assert digest is not None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "in_flight" in r.message and WEEK_OF in r.message for r in warnings
    ), f"expected an in-flight claim override warning, got: {[r.message for r in warnings]}"


@pytest.mark.asyncio
async def test_run_weekly_does_not_warn_when_no_claim_exists(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_process()
        await pipeline.run_weekly(week_of=WEEK_OF)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
async def test_run_weekly_does_not_warn_when_claim_is_stale(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    stale_at = (datetime.now(UTC) - pipeline._CLAIM_TTL - timedelta(minutes=1)).isoformat()
    await wired.save_app_state(
        f"claim_{DigestKind.WEEKLY.value}", {"date": WEEK_OF, "at": stale_at}
    )

    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_process()
        await pipeline.run_weekly(week_of=WEEK_OF)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
async def test_run_weekly_does_not_warn_when_claim_is_for_a_different_date(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    await wired.claim_digest_run(DigestKind.WEEKLY, "2026-06-08")

    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_process()
        await pipeline.run_weekly(week_of=WEEK_OF)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
async def test_run_weekly_if_missing_does_not_warn_about_its_own_just_won_claim(
    wired: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """Weekly counterpart of
    test_run_daily_if_missing_does_not_warn_about_its_own_just_won_claim above.

    Not redundant with the daily test: the thing that could break here is not
    `_warn_if_claim_in_flight` (shared code, already covered by the daily
    case) — it is the one-line `_claim_already_acquired=True` argument in
    `run_weekly_if_missing`'s `run=` lambda specifically. Drop that argument
    and every weekly catch-up would cry wolf with a false "may double-deliver"
    warning, and nothing in the daily test would catch it.
    """
    with caplog.at_level(logging.WARNING, logger="aidigest.flows"):
        await pipeline.run_ingest()
        await pipeline.run_process()
        digest = await pipeline.run_weekly_if_missing(week_of=WEEK_OF, deliver=False)

    assert digest is not None
    assert not [
        r for r in caplog.records if r.levelname == "WARNING"
    ], "run_weekly_if_missing must never warn about the claim it just atomically won"


@pytest.mark.asyncio
async def test_run_weekly_records_delivery_flags_email_only(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weekly has no Telegram channel — `telegram` must always record False."""

    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        return True

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: _settings_with_channels(email=True, telegram=False)
    )
    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)

    await pipeline.run_ingest()
    await pipeline.run_process()
    await pipeline.run_weekly(week_of=WEEK_OF, deliver=True)

    delivery = await wired.get_delivery(DigestKind.WEEKLY)
    assert delivery is not None
    assert delivery["email"] is True
    assert delivery["telegram"] is False
    assert delivery["delivered"] is True


@pytest.mark.asyncio
async def test_run_weekly_records_delivery_before_wiki_export(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weekly counterpart of test_run_daily_records_delivery_before_wiki_export
    above — see that test's docstring for the crash-window rationale."""
    settings_with_wiki = _settings_with_channels(email=True, telegram=False).model_copy(
        update={"wiki_dir": "/tmp/unused-wiki-dir-for-this-test"}
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings_with_wiki)

    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        return True

    seen: dict[str, object] = {}

    def _spy_wiki_export_weekly(
        digest: WeeklyDigest, *, wiki_dir: str, daily_dates: list[str] | None = None
    ) -> list:
        seen["called"] = True
        seen["delivery_at_wiki_time"] = wired.state.get(f"delivery_{DigestKind.WEEKLY.value}")
        return []

    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)
    monkeypatch.setattr(pipeline, "wiki_export_weekly", _spy_wiki_export_weekly)

    await pipeline.run_ingest()
    await pipeline.run_process()
    await pipeline.run_weekly(week_of=WEEK_OF, deliver=True)

    assert seen.get("called") is True, "the wiki export spy never ran"
    assert seen.get("delivery_at_wiki_time") is not None, (
        "record_delivery must be awaited before wiki export runs"
    )


@pytest.mark.asyncio
async def test_run_weekly_records_delivered_true_and_logs_channels_none_when_email_not_configured(
    wired: FakeRepo, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _fake_send_email(*, subject: str, html: str, text: str | None = None) -> bool:
        return False

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: _settings_with_channels(email=False, telegram=False)
    )
    monkeypatch.setattr(pipeline, "send_email", _fake_send_email)

    await pipeline.run_ingest()
    await pipeline.run_process()
    with caplog.at_level(logging.INFO, logger="aidigest.flows"):
        await pipeline.run_weekly(week_of=WEEK_OF, deliver=True)

    delivery = await wired.get_delivery(DigestKind.WEEKLY)
    assert delivery is not None
    assert delivery["email"] is False
    assert delivery["telegram"] is False
    assert delivery["delivered"] is True

    ok_lines = [
        r.message for r in caplog.records if r.message.startswith("step=deliver_weekly status=ok")
    ]
    assert ok_lines, "no completion log line for the deliver_weekly step"
    assert "channels=none" in ok_lines[-1]


# --------------------------------------------------------------------------- #
# CLI: --if-missing wiring (scripts/run_daily.py, scripts/run_weekly.py)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_daily_cli_if_missing_calls_the_catchup_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_daily as cli

    called: dict[str, object] = {}

    async def _fake_if_missing(*, date: str | None = None, deliver: bool = False) -> None:
        called["date"] = date
        called["deliver"] = deliver
        return None

    async def _fail_run_daily(*, date: str | None = None, deliver: bool = False) -> DailyDigest:
        raise AssertionError("plain run_daily must not run when --if-missing is set")

    monkeypatch.setattr(cli, "run_daily_if_missing", _fake_if_missing)
    monkeypatch.setattr(cli, "run_daily", _fail_run_daily)

    await cli._main("2026-08-06", True, True)

    assert called == {"date": "2026-08-06", "deliver": True}


@pytest.mark.asyncio
async def test_run_daily_cli_if_missing_none_prints_skip_line_and_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.run_daily as cli

    async def _fake_if_missing(*, date: str | None = None, deliver: bool = False) -> None:
        return None

    monkeypatch.setattr(cli, "run_daily_if_missing", _fake_if_missing)

    await cli._main("2026-08-06", True, True)  # must return normally, no exception -> exit 0

    out = capsys.readouterr().out
    assert out.strip() == (
        "skipped: 2026-08-06 — daily digest already shipped or a run is in flight"
    )


@pytest.mark.asyncio
async def test_run_daily_cli_if_missing_none_with_no_date_prints_today(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No --date given: the skip line falls back to the literal 'today' rather
    than resolving the actual date (which stays private to pipeline.py — it is
    already in the run log via _catch_up's date=%s on both skip paths)."""
    import scripts.run_daily as cli

    async def _fake_if_missing(*, date: str | None = None, deliver: bool = False) -> None:
        return None

    monkeypatch.setattr(cli, "run_daily_if_missing", _fake_if_missing)

    await cli._main(None, True, True)

    out = capsys.readouterr().out
    assert out.strip() == "skipped: today — daily digest already shipped or a run is in flight"


@pytest.mark.asyncio
async def test_run_weekly_cli_if_missing_calls_the_catchup_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_weekly as cli

    called: dict[str, object] = {}

    async def _fake_if_missing(*, week_of: str | None = None, deliver: bool = False) -> None:
        called["week_of"] = week_of
        called["deliver"] = deliver
        return None

    async def _fail_run_weekly(
        *, week_of: str | None = None, deliver: bool = False
    ) -> WeeklyDigest:
        raise AssertionError("plain run_weekly must not run when --if-missing is set")

    monkeypatch.setattr(cli, "run_weekly_if_missing", _fake_if_missing)
    monkeypatch.setattr(cli, "run_weekly", _fail_run_weekly)

    await cli._main(WEEK_OF, True, True)

    assert called == {"week_of": WEEK_OF, "deliver": True}


@pytest.mark.asyncio
async def test_run_weekly_cli_if_missing_none_prints_skip_line_and_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.run_weekly as cli

    async def _fake_if_missing(*, week_of: str | None = None, deliver: bool = False) -> None:
        return None

    monkeypatch.setattr(cli, "run_weekly_if_missing", _fake_if_missing)

    await cli._main(WEEK_OF, True, True)  # must return normally, no exception -> exit 0

    out = capsys.readouterr().out
    assert out.strip() == (
        f"skipped: {WEEK_OF} — weekly digest already shipped or a run is in flight"
    )


@pytest.mark.asyncio
async def test_run_weekly_cli_if_missing_none_with_no_week_of_prints_this_week(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No --week-of given: the skip line falls back to the literal 'this week'
    rather than resolving the actual week (which stays private to pipeline.py —
    it is already in the run log via _catch_up's date=%s on both skip paths)."""
    import scripts.run_weekly as cli

    async def _fake_if_missing(*, week_of: str | None = None, deliver: bool = False) -> None:
        return None

    monkeypatch.setattr(cli, "run_weekly_if_missing", _fake_if_missing)

    await cli._main(None, True, True)

    out = capsys.readouterr().out
    assert out.strip() == "skipped: this week — weekly digest already shipped or a run is in flight"


# --------------------------------------------------------------------------- #
# Workflow integrity — every schedule/dispatch choice must be wired to a step
# --------------------------------------------------------------------------- #


def test_workflow_every_schedule_and_dispatch_choice_is_referenced_by_a_step() -> None:
    """Guards against adding a schedule or workflow_dispatch choice that silently
    runs nothing — a generalized version of the class of gap behind the
    2026-08-06 incident (a slot that never actually invokes any step).
    """
    doc = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML 1.1 booleans: a bare `on:` key parses as the boolean True, not "on".
    on = doc.get("on", doc.get(True))
    assert on is not None, "workflow has no on: trigger block"

    crons = [entry["cron"] for entry in on["schedule"]]
    assert crons, "workflow has no schedule entries"
    choices = on["workflow_dispatch"]["inputs"]["job"]["options"]
    assert choices, "workflow_dispatch has no job choices"

    steps = [step for job in doc["jobs"].values() for step in job.get("steps", [])]
    if_exprs = " ".join(step.get("if", "") for step in steps)

    for cron in crons:
        assert (
            cron in if_exprs
        ), f"cron {cron!r} is not referenced by any step's if: (dead schedule)"
    for choice in choices:
        needle = f"'{choice}'"
        assert (
            needle in if_exprs
        ), f"workflow_dispatch choice {choice!r} is not referenced by any step's if:"


def test_workflow_scheduled_daily_and_weekly_steps_use_if_missing_but_force_steps_do_not() -> None:
    """Regression guard for the cron-delay duplicate-send hazard: GitHub delays
    scheduled runs by ~1-2.5h routinely on this repo, so a delay past 3h would
    let a catch-up slot fire BEFORE the (now-late) primary slot. If the primary
    slot's step were left ungated, it would then generate + deliver a SECOND
    time once it finally runs — a duplicate digest email, not just a duplicate
    no-op. So the primary cron and its catch-up crons must share ONE step that
    passes --if-missing; only the workflow_dispatch manual force path (an
    explicit "regenerate and re-send anyway") may omit it.
    """
    doc = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps_by_name = {
        step["name"]: step
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
        if "name" in step
    }

    for name, primary_cron in (("Daily digest", "0 14 * * *"), ("Weekly digest", "0 15 * * 0")):
        step = steps_by_name[name]
        assert "--if-missing" in step.get("run", ""), f"{name!r} must pass --if-missing"
        assert primary_cron in step.get(
            "if", ""
        ), f"{name!r} must also gate the PRIMARY slot ({primary_cron}), not just catch-up"

    for name in ("Daily digest (force)", "Weekly digest (force)"):
        step = steps_by_name[name]
        run = step.get("run", "")
        assert "--if-missing" not in run, f"{name!r} (manual force) must NOT pass --if-missing"
        assert "--deliver" in run, f"{name!r} should still deliver"
        assert "github.event.schedule" not in step.get(
            "if", ""
        ), f"{name!r} is a manual-only force path and must never fire on a schedule"


def test_claim_ttl_exceeds_the_pipeline_job_timeout() -> None:
    """The invariant: `_CLAIM_TTL` must be strictly greater than the workflow
    pipeline job's `timeout-minutes`.

    GitHub hard-kills a run at `timeout-minutes`, so a claim older than that
    CANNOT belong to a still-live run — TTL <= timeout would let a legitimately
    slow (but healthy) run's claim go stale WHILE the run is still executing, so
    a catch-up would see "stale", win the CAS, and generate + deliver a second
    time underneath the still-running original. Parses timeout-minutes out of
    digest.yml directly so whoever changes either number independently gets a
    failing test instead of a silent double-send.
    """
    doc = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    timeout_minutes = doc["jobs"]["pipeline"]["timeout-minutes"]
    assert pipeline._CLAIM_TTL > timedelta(minutes=timeout_minutes), (
        f"_CLAIM_TTL ({pipeline._CLAIM_TTL}) must exceed the pipeline job's "
        f"timeout-minutes ({timeout_minutes}) or a slow-but-healthy run's own "
        "claim can go stale while it is still executing"
    )
