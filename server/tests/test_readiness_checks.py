"""/health/ready aggregation: registered checks, failures, timeouts and concurrency."""

import asyncio
import time

import pytest
from admission_helpers import AUTH, CountingProcessor, FakeStats, build_app, fast_settings, running
from conftest import idle_worker_stats

from app.main import create_app

pytestmark = pytest.mark.anyio


async def ok() -> bool:
    return True


async def failing() -> bool:
    return False


async def raising() -> bool:
    raise ConnectionError("SECRET-INTERNAL-HOST refused")


async def hanging() -> bool:
    await asyncio.sleep(30)
    return True


async def ready_response(redis, checks, **overrides):
    app = build_app(fast_settings(**overrides), redis, CountingProcessor(), FakeStats(), checks)
    async with running(app) as http:
        return await http.get("/health/ready")


async def test_default_checks_cover_redis_both_workers_s3_and_ffmpeg(redis):
    """Criterion 11."""
    app = create_app(
        fast_settings(), redis=redis, processor=CountingProcessor(), worker_stats=idle_worker_stats
    )
    async with running(app):
        assert set(app.state.readiness_checks) == {
            "redis",
            "audio_worker",
            "video_worker",
            "s3",
            "ffmpeg",
        }


async def test_empty_injected_checks_leave_only_redis(redis):
    """Criterion 11."""
    app = build_app(fast_settings(), redis, CountingProcessor(), FakeStats(), {})
    async with running(app):
        assert set(app.state.readiness_checks) == {"redis"}


async def test_injected_checks_are_added_alongside_redis(redis):
    """Criterion 11."""
    app = build_app(fast_settings(), redis, CountingProcessor(), FakeStats(), {"extra": ok})
    async with running(app):
        assert set(app.state.readiness_checks) == {"redis", "extra"}


async def test_all_checks_passing_returns_ready(redis):
    """Criterion 12."""
    response = await ready_response(redis, {"audio_worker": ok, "s3": ok})
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"redis": True, "audio_worker": True, "s3": True},
    }


async def test_failed_checks_return_503_listing_exactly_the_failed_names_sorted(redis):
    """Criterion 12: a False, a raise and a timeout are all failures."""
    checks = {"s3": failing, "video_worker": raising, "ffmpeg": hanging, "audio_worker": ok}
    response = await ready_response(redis, checks, dependency_timeout_seconds=0.2)
    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) >= 1
    error = response.json()["error"]
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert error["details"]["failed_checks"] == ["ffmpeg", "s3", "video_worker"]
    assert "SECRET-INTERNAL-HOST" not in response.text


async def test_redis_failure_is_reported_alongside_other_failures(redis, redis_server):
    """Criterion 12."""
    redis_server.connected = False
    response = await ready_response(redis, {"s3": failing})
    assert response.status_code == 503
    assert response.json()["error"]["details"]["failed_checks"] == ["redis", "s3"]


async def test_checks_run_concurrently(redis):
    """Criterion 12: three 0.5 s checks finish well under the 1.5 s sequential cost."""

    async def slow_ok() -> bool:
        await asyncio.sleep(0.5)
        return True

    checks = {"a": slow_ok, "b": slow_ok, "c": slow_ok}
    started = time.monotonic()
    response = await ready_response(redis, checks)
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 1.2


async def test_check_exceeding_timeout_is_failed_without_waiting_for_it(redis):
    """Criterion 12."""
    started = time.monotonic()
    response = await ready_response(
        redis, {"audio_worker": hanging}, dependency_timeout_seconds=0.2
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["error"]["details"]["failed_checks"] == ["audio_worker"]
    assert elapsed < 2


async def test_readiness_does_not_require_api_key(redis):
    """Criterion 12: probes stay open (existing behavior), with or without a key."""
    app = build_app(fast_settings(), redis, CountingProcessor(), FakeStats(), {})
    async with running(app) as http:
        assert (await http.get("/health/ready")).status_code == 200
        assert (await http.get("/health/ready", headers=AUTH)).status_code == 200
