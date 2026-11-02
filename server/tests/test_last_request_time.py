"""`started_at` and `last_request_at` in /health/live, used by the idle auto-stop script."""

import asyncio
from datetime import UTC, datetime, timedelta

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


def parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    return parsed


async def live(http) -> dict:
    response = await http.get("/health/live")
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def make(redis):
    def _make(settings=None, processor=None, stats=None):
        return build_app(
            settings or fast_settings(),
            redis,
            processor or CountingProcessor(),
            stats or FakeStats(),
        )

    return _make


async def test_live_reports_start_time_and_no_requests_yet(make):
    """Criterion 16."""
    before = datetime.now(UTC) - timedelta(milliseconds=2)
    async with running(make()) as http:
        body = await live(http)
    assert set(body) == {"status", "started_at", "last_request_at"}
    assert body["status"] == "ok"
    assert body["last_request_at"] is None
    assert before <= parse(body["started_at"]) <= datetime.now(UTC)


async def test_last_request_at_is_set_after_a_valid_analysis(make):
    """Criterion 16."""
    async with running(make()) as http:
        started = (await live(http))["started_at"]
        before = datetime.now(UTC).replace(microsecond=0)
        assert (await http.post("/v1/analyses", json=new_body(), headers=AUTH)).status_code == 200
        body = await live(http)
    assert before <= parse(body["last_request_at"]) <= datetime.now(UTC)
    assert body["started_at"] == started


async def test_last_request_at_advances_with_each_request(make):
    """Criterion 16."""
    async with running(make()) as http:
        await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        first = parse((await live(http))["last_request_at"])
        await asyncio.sleep(0.02)
        await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        second = parse((await live(http))["last_request_at"])
    assert second > first


async def test_last_request_at_is_updated_by_a_rate_limited_request(make):
    """Criterion 16: a 429 still counts as activity."""
    settings = fast_settings(request_deadline_seconds=60)
    stats = FakeStats(audio=(0, 100.0))
    async with running(make(settings, stats=stats)) as http:
        response = await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        assert response.status_code == 429
        assert (await live(http))["last_request_at"] is not None


async def test_last_request_at_is_updated_by_a_replay(make):
    """Criterion 16."""
    body = new_body()
    async with running(make()) as http:
        await http.post("/v1/analyses", json=body, headers=AUTH)
        first = parse((await live(http))["last_request_at"])
        await asyncio.sleep(0.02)
        replay = await http.post("/v1/analyses", json=body, headers=AUTH)
        assert replay.headers["X-MAI-Replayed"] == "true"
        assert parse((await live(http))["last_request_at"]) > first


async def test_last_request_at_is_updated_by_an_in_progress_conflict(make):
    """Criterion 16."""
    gate = asyncio.Event()
    processor = CountingProcessor(gate)
    body = new_body()
    async with running(make(processor=processor)) as http:
        running_request = asyncio.create_task(http.post("/v1/analyses", json=body, headers=AUTH))
        await wait_until(lambda: processor.calls == 1)
        first = parse((await live(http))["last_request_at"])
        await asyncio.sleep(0.02)
        conflict = await http.post("/v1/analyses", json=body, headers=AUTH)
        assert conflict.status_code == 409
        assert parse((await live(http))["last_request_at"]) > first
        gate.set()
        await running_request


async def test_health_probes_do_not_update_last_request_at(make):
    """Criterion 16."""
    async with running(make()) as http:
        await http.get("/health/live")
        await http.get("/health/ready")
        assert (await live(http))["last_request_at"] is None
        await http.post("/v1/analyses", json=new_body(), headers=AUTH)
        recorded = (await live(http))["last_request_at"]
        await asyncio.sleep(0.02)
        await http.get("/health/ready")
        await http.get("/health/live")
        assert (await live(http))["last_request_at"] == recorded


async def test_unauthorized_request_does_not_update_last_request_at(make):
    """Criterion 16."""
    async with running(make()) as http:
        no_key = await http.post("/v1/analyses", json=new_body())
        bad_key = await http.post("/v1/analyses", json=new_body(), headers={"X-API-Key": "nope"})
        assert no_key.status_code == 401
        assert bad_key.status_code == 401
        assert (await live(http))["last_request_at"] is None


async def test_invalid_body_does_not_update_last_request_at(make):
    """Criterion 16."""
    async with running(make()) as http:
        invalid = await http.post("/v1/analyses", json={"request_id": "nope"}, headers=AUTH)
        assert invalid.status_code == 400
        assert (await live(http))["last_request_at"] is None
