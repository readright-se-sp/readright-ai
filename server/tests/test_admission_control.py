"""Admission in POST /v1/analyses: deadline estimate, in-flight cap, stats failures, replay."""

import asyncio
import logging

import pytest
from admission_helpers import (
    AUTH,
    CountingProcessor,
    FakeStats,
    build_app,
    fast_settings,
    new_body,
    running,
    wait_until,
)

pytestmark = pytest.mark.anyio


def rejections(caplog):
    return [r for r in caplog.records if r.getMessage() == "admission rejected"]


@pytest.fixture
def capture(caplog):
    """Attach caplog after the app is built: create_app replaces the root handlers."""
    added = []

    def attach():
        root = logging.getLogger()
        root.addHandler(caplog.handler)
        added.append(root)
        caplog.set_level(logging.DEBUG)

    yield attach
    for root in added:
        root.removeHandler(caplog.handler)


# Deadline estimate -----------------------------------------------------------------------------


async def test_estimate_over_deadline_is_rejected_with_retry_after(redis, caplog, capture):
    """Criterion 6: estimate 15 + 40 + 10 = 65 against a 60 s deadline."""
    settings = fast_settings(
        request_deadline_seconds=60,
        stage_estimate_download_seconds=15,
        stage_estimate_upload_seconds=10,
    )
    processor = CountingProcessor()
    stats = FakeStats(audio=(0, 40.0))
    app = build_app(settings, redis, processor, stats)
    capture()
    async with running(app) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) in (5, 6)  # ceil(65 - just under 60)
    assert processor.calls == 0
    [record] = rejections(caplog)
    assert record.levelname == "WARNING"
    assert record.reason == "deadline"
    assert record.estimate_seconds == pytest.approx(65, abs=0.01)
    assert 59 < record.remaining_seconds <= 60


async def test_estimate_within_deadline_is_admitted(redis):
    """Criterion 6: 15 + 40 + 10 = 65 fits a 115 s deadline."""
    settings = fast_settings(stage_estimate_download_seconds=15, stage_estimate_upload_seconds=10)
    stats = FakeStats(audio=(0, 40.0))
    async with running(build_app(settings, redis, CountingProcessor(), stats)) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert response.status_code == 200


async def test_deadline_rejection_makes_no_claim_so_retry_is_processed(redis):
    """Criterion 6: the same request_id succeeds once load drops, with no 409."""
    settings = fast_settings(request_deadline_seconds=60)
    processor = CountingProcessor()
    stats = FakeStats(audio=(0, 100.0))
    body = new_body()
    async with running(build_app(settings, redis, processor, stats)) as http:
        first = await http.post("/v1/analyses", json=body, headers=AUTH)
        assert first.status_code == 429
        assert processor.calls == 0
        stats.by_lane["audio"] = (0, 0.5)
        second = await http.post("/v1/analyses", json=body, headers=AUTH)
    assert second.status_code == 200
    assert "X-MAI-Replayed" not in second.headers
    assert processor.calls == 1


async def test_queue_on_worker_counts_towards_the_estimate(redis):
    """Criterion 6: (9+1) * 10 s = 100 s against a 60 s deadline."""
    settings = fast_settings(request_deadline_seconds=60)
    stats = FakeStats(video=(9, 10.0))
    async with running(build_app(settings, redis, CountingProcessor(), stats)) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 39


# In-flight cap ---------------------------------------------------------------------------------


async def test_third_request_is_rejected_at_the_cap_then_admitted_after_release(
    redis, caplog, capture
):
    """Criterion 7."""
    settings = fast_settings(max_in_flight=2)
    gate = asyncio.Event()
    processor = CountingProcessor(gate)
    app = build_app(settings, redis, processor, FakeStats())
    capture()
    async with running(app) as http:
        first = asyncio.create_task(http.post("/v1/analyses", json=new_body(), headers=AUTH))
        second = asyncio.create_task(http.post("/v1/analyses", json=new_body(), headers=AUTH))
        await wait_until(lambda: processor.calls == 2)

        third = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "RATE_LIMITED"
        assert int(third.headers["Retry-After"]) >= 1
        assert processor.calls == 2
        [record] = rejections(caplog)
        assert record.levelname == "WARNING"
        assert record.reason == "in_flight_cap"

        gate.set()
        assert (await first).status_code == 200
        assert (await second).status_code == 200

        fourth = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert fourth.status_code == 200


async def test_cap_rejection_makes_no_claim_so_same_request_id_runs_later(redis):
    """Criterion 7."""
    settings = fast_settings(max_in_flight=1)
    gate = asyncio.Event()
    processor = CountingProcessor(gate)
    rejected_body = new_body()
    async with running(build_app(settings, redis, processor, FakeStats())) as http:
        blocker = asyncio.create_task(http.post("/v1/analyses", json=new_body(), headers=AUTH))
        await wait_until(lambda: processor.calls == 1)
        rejected = await http.post("/v1/analyses", json=rejected_body, headers=AUTH)
        assert rejected.status_code == 429
        gate.set()
        await blocker
        retry = await http.post("/v1/analyses", json=rejected_body, headers=AUTH)
    assert retry.status_code == 200


async def test_cap_slot_is_freed_after_processor_error(redis):
    """Criterion 7."""
    processor = CountingProcessor()
    processor.error = RuntimeError("boom")
    async with running(
        build_app(fast_settings(max_in_flight=1), redis, processor, FakeStats())
    ) as http:
        for _ in range(3):  # a leaked slot would turn the 2nd into a 429
            response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
            assert response.status_code == 500
        processor.error = None
        ok = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert ok.status_code == 200


async def test_cap_slot_is_freed_after_deadline_timeout(redis):
    """Criterion 7."""
    settings = fast_settings(max_in_flight=1, request_deadline_seconds=0.3)
    gate = asyncio.Event()  # never set: the processor hangs until the deadline cancels it
    processor = CountingProcessor(gate)
    async with running(build_app(settings, redis, processor, FakeStats())) as http:
        timed_out = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        assert timed_out.status_code == 504
        processor.gate = None
        ok = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert ok.status_code == 200


async def test_cap_slot_is_freed_after_success(redis):
    """Criterion 7."""
    async with running(
        build_app(fast_settings(max_in_flight=1), redis, CountingProcessor(), FakeStats())
    ) as http:
        for _ in range(3):
            response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
            assert response.status_code == 200


async def test_in_flight_requests_raise_the_estimate_for_a_burst(redis):
    """Criterion 3/7: a request admitted while another is downloading sees queue >= 1."""
    # Worker queues are 0, avg 10 s: alone 10 s fits a 15 s deadline, behind one other it is 20 s.
    settings = fast_settings(request_deadline_seconds=15, max_in_flight=5)
    gate = asyncio.Event()
    processor = CountingProcessor(gate)
    stats = FakeStats(audio=(0, 10.0), video=(0, 10.0))
    async with running(build_app(settings, redis, processor, stats)) as http:
        first = asyncio.create_task(http.post("/v1/analyses", json=new_body(), headers=AUTH))
        await wait_until(lambda: processor.calls == 1)
        second = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        gate.set()
        assert (await first).status_code == 200
    assert second.status_code == 429


# Worker stats failures -------------------------------------------------------------------------


async def test_unreachable_worker_stats_return_503_without_claiming(redis):
    """Criterion 8."""
    processor = CountingProcessor()
    stats = FakeStats()
    stats.error = ConnectionError("worker down")
    body = new_body()
    async with running(build_app(fast_settings(), redis, processor, stats)) as http:
        failed = await http.post("/v1/analyses", json=body, headers=AUTH)
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert int(failed.headers["Retry-After"]) >= 1
        assert "worker down" not in failed.text
        assert processor.calls == 0

        stats.error = None
        retry = await http.post("/v1/analyses", json=body, headers=AUTH)
    assert retry.status_code == 200  # no leftover claim, so not 409


# Replay ----------------------------------------------------------------------------------------


async def test_stored_result_is_replayed_even_when_at_the_cap(redis):
    """Criterion 5."""
    settings = fast_settings(max_in_flight=1)
    gate = asyncio.Event()
    processor = CountingProcessor(gate)
    stats = FakeStats()
    body = new_body()
    async with running(build_app(settings, redis, processor, stats)) as http:
        gate.set()
        first = await http.post("/v1/analyses", json=body, headers=AUTH)
        assert first.status_code == 200
        gate.clear()
        blocker = asyncio.create_task(http.post("/v1/analyses", json=new_body(), headers=AUTH))
        await wait_until(lambda: processor.calls == 2)
        stats.calls.clear()

        replay = await http.post("/v1/analyses", json=body, headers=AUTH)
        gate.set()
        await blocker
    assert replay.status_code == 200
    assert replay.headers["X-MAI-Replayed"] == "true"
    assert replay.json() == first.json()
    assert stats.calls == []
    assert processor.calls == 2


async def test_stored_result_is_replayed_even_when_estimate_exceeds_deadline(redis):
    """Criterion 5."""
    settings = fast_settings(request_deadline_seconds=60)
    stats = FakeStats()
    body = new_body()
    async with running(build_app(settings, redis, CountingProcessor(), stats)) as http:
        assert (await http.post("/v1/analyses", json=body, headers=AUTH)).status_code == 200
        stats.by_lane["audio"] = (50, 100.0)
        stats.calls.clear()
        replay = await http.post("/v1/analyses", json=body, headers=AUTH)
    assert replay.status_code == 200
    assert replay.headers["X-MAI-Replayed"] == "true"
    assert stats.calls == []


async def test_stored_result_is_replayed_when_worker_stats_are_failing(redis):
    """Criterion 5."""
    stats = FakeStats()
    body = new_body()
    async with running(build_app(fast_settings(), redis, CountingProcessor(), stats)) as http:
        assert (await http.post("/v1/analyses", json=body, headers=AUTH)).status_code == 200
        stats.error = ConnectionError("down")
        stats.calls.clear()
        replay = await http.post("/v1/analyses", json=body, headers=AUTH)
    assert replay.status_code == 200
    assert replay.headers["X-MAI-Replayed"] == "true"
    assert stats.calls == []


async def test_admission_runs_before_the_processor_and_claim_for_new_requests(redis):
    """Criterion 5: stats are consulted once per lane for a new request."""
    stats = FakeStats()
    async with running(build_app(fast_settings(), redis, CountingProcessor(), stats)) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert response.status_code == 200
    assert sorted(stats.calls) == ["audio", "video"]
