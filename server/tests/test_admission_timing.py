"""Admission timing: fresh deadline after the stats fetch, and a bounded stats fetch."""

import asyncio
import time

import pytest
from admission_helpers import (
    AUTH,
    CountingProcessor,
    FakeStats,
    build_app,
    fast_settings,
    new_body,
    running,
)

pytestmark = pytest.mark.anyio


class SlowStats(FakeStats):
    def __init__(self, delay: float, **kwargs):
        super().__init__(**kwargs)
        self.delay = delay

    async def __call__(self, lane):
        await asyncio.sleep(self.delay)
        return await super().__call__(lane)


@pytest.fixture
def capture(caplog):
    import logging

    root = logging.getLogger()

    def attach():
        root.addHandler(caplog.handler)
        caplog.set_level(logging.DEBUG)

    yield attach
    root.removeHandler(caplog.handler)


async def test_estimate_fits_before_the_stats_fetch_but_not_after_it(redis, caplog, capture):
    """Criterion 19: estimate 0.6 s vs a 0.8 s deadline; a 0.3 s fetch leaves ~0.5 s."""
    settings = fast_settings(request_deadline_seconds=0.8)
    processor = CountingProcessor()
    stats = SlowStats(0.3, audio=(0, 0.6))
    app = build_app(settings, redis, processor, stats)
    capture()
    async with running(app) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert processor.calls == 0
    [record] = [r for r in caplog.records if r.getMessage() == "admission rejected"]
    assert record.reason == "deadline"
    assert record.remaining_seconds < 0.55  # measured after the fetch, not the full 0.8


async def test_same_estimate_is_admitted_when_the_stats_fetch_is_instant(redis):
    """Criterion 19: control for the test above."""
    settings = fast_settings(request_deadline_seconds=0.8)
    stats = SlowStats(0.0, audio=(0, 0.6))
    async with running(build_app(settings, redis, CountingProcessor(), stats)) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert response.status_code == 200


async def test_hanging_stats_source_gives_503_within_dependency_timeout(redis):
    """Criterion 20."""
    settings = fast_settings(dependency_timeout_seconds=0.2)
    processor = CountingProcessor()

    async def hangs(lane):
        await asyncio.Event().wait()

    async with running(build_app(settings, redis, processor, hangs)) as http:
        started = time.monotonic()
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert int(response.headers["Retry-After"]) >= 1
    assert elapsed < 1.5
    assert processor.calls == 0


async def test_in_flight_slot_is_free_after_a_stats_timeout(redis):
    """Criterion 20: with max_in_flight=1 a later request is still admitted."""
    settings = fast_settings(dependency_timeout_seconds=0.2, max_in_flight=1)
    hang = True

    async def sometimes_hangs(lane):
        if hang:
            await asyncio.Event().wait()
        return await FakeStats()(lane)

    async with running(build_app(settings, redis, CountingProcessor(), sometimes_hangs)) as http:
        failed = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        assert failed.status_code == 503
        hang = False
        ok = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
    assert ok.status_code == 200
