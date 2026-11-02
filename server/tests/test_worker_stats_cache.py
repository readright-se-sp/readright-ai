"""Default worker stats source: HTTP fetch from both workers, cached and single-flight.

Built through `create_app(...)` without `worker_stats=`, with worker `/stats` mocked by respx.
"""

import asyncio

import httpx
import pytest
import respx
from admission_helpers import AUTH, CountingProcessor, fast_settings, new_body, running, wait_until

from app.main import create_app

pytestmark = pytest.mark.anyio

AUDIO_STATS = "http://audio.test:8001/stats"
VIDEO_STATS = "http://video.test:8002/stats"
OK_BODY = {"queue_length": 0, "avg_processing_seconds": 0.01}


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        mock.audio = mock.get(AUDIO_STATS).respond(200, json=OK_BODY)
        mock.video = mock.get(VIDEO_STATS).respond(200, json=OK_BODY)
        yield mock


def make_app(redis, processor=None, **overrides):
    return create_app(
        fast_settings(**overrides),
        redis=redis,
        processor=processor or CountingProcessor(),
        dependency_checks={},
    )


async def post(http, **kwargs):
    return await http.post("/v1/analyses", json=new_body(), headers=AUTH)


async def test_default_source_fetches_stats_from_both_workers(redis, router):
    """Criterion 9."""
    processor = CountingProcessor()
    async with running(make_app(redis, processor)) as http:
        response = await post(http)
    assert response.status_code == 200
    assert router.audio.call_count == 1
    assert router.video.call_count == 1
    assert processor.calls == 1


async def test_default_source_uses_reported_queue_and_average(redis, router):
    """Criterion 9: the fetched values feed the estimate (10 s * 10 > 60 s deadline)."""
    router.audio.respond(200, json={"queue_length": 9, "avg_processing_seconds": 10.0})
    processor = CountingProcessor()
    async with running(make_app(redis, processor, request_deadline_seconds=60)) as http:
        response = await post(http)
    assert response.status_code == 429
    assert processor.calls == 0


async def test_default_source_accepts_null_average(redis, router):
    """Criterion 9."""
    router.audio.respond(200, json={"queue_length": 0, "avg_processing_seconds": None})
    async with running(make_app(redis)) as http:
        response = await post(http)
    assert response.status_code == 200


async def test_default_source_passes_dependency_timeout_to_http_client(redis, router):
    """Criterion 9: assumes the httpx timeout is set from `dependency_timeout_seconds`."""
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json=OK_BODY)

    router.audio.mock(side_effect=handler)
    async with running(make_app(redis, dependency_timeout_seconds=1.5)) as http:
        response = await post(http)
    assert response.status_code == 200
    assert seen
    assert 1.5 in {value for value in seen[0].values() if value is not None}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"detail": "SECRET-WORKER-DETAIL"}),
        httpx.Response(404),
        httpx.Response(200, json={"queue_length": -1, "avg_processing_seconds": 1.0}),
        httpx.Response(200, json={"queue_length": 0, "avg_processing_seconds": -3}),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, content=b"not json"),
    ],
)
async def test_bad_stats_response_fails_closed_with_503(redis, router, response):
    """Criterion 9."""
    router.video.mock(return_value=response)
    processor = CountingProcessor()
    async with running(make_app(redis, processor)) as http:
        result = await post(http)
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "Retry-After" in result.headers
    assert "SECRET-WORKER-DETAIL" not in result.text
    assert processor.calls == 0


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_unreachable_stats_endpoint_fails_closed_with_503(redis, router, error):
    """Criterion 9."""
    router.audio.mock(side_effect=error)
    processor = CountingProcessor()
    async with running(make_app(redis, processor)) as http:
        result = await post(http)
    assert result.status_code == 503
    assert processor.calls == 0


# Cache -----------------------------------------------------------------------------------------


async def test_second_request_within_ttl_reuses_cached_stats(redis, router):
    """Criterion 10."""
    async with running(make_app(redis, stats_cache_seconds=30)) as http:
        assert (await post(http)).status_code == 200
        assert (await post(http)).status_code == 200
    assert router.audio.call_count == 1
    assert router.video.call_count == 1


async def test_request_after_ttl_fetches_again(redis, router):
    """Criterion 10."""
    async with running(make_app(redis, stats_cache_seconds=0.2)) as http:
        assert (await post(http)).status_code == 200
        await asyncio.sleep(0.35)
        assert (await post(http)).status_code == 200
    assert router.audio.call_count == 2
    assert router.video.call_count == 2


async def test_zero_ttl_never_reuses_stats(redis, router):
    """Criterion 10."""
    async with running(make_app(redis, stats_cache_seconds=0)) as http:
        assert (await post(http)).status_code == 200
        assert (await post(http)).status_code == 200
    assert router.audio.call_count == 2


async def test_concurrent_cold_requests_share_one_fetch_per_lane(redis, router):
    """Criterion 10."""
    gate = asyncio.Event()

    started = {"audio": 0, "video": 0}

    async def slow(request):
        started["audio" if request.url.host == "audio.test" else "video"] += 1
        await gate.wait()
        return httpx.Response(200, json=OK_BODY)

    router.audio.mock(side_effect=slow)
    router.video.mock(side_effect=slow)
    count = 5
    async with running(make_app(redis, stats_cache_seconds=30, max_in_flight=count)) as http:
        tasks = [asyncio.create_task(post(http)) for _ in range(count)]
        await wait_until(lambda: started["audio"] >= 1 and started["video"] >= 1)
        await asyncio.sleep(0.05)  # let the other requests reach admission
        gate.set()
        results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results] == [200] * count
    assert started == {"audio": 1, "video": 1}


async def test_failed_fetch_is_not_cached(redis, router):
    """Criterion 10."""
    router.audio.mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=OK_BODY)],
    )
    async with running(make_app(redis, stats_cache_seconds=30)) as http:
        failed = await post(http)
        assert failed.status_code == 503
        recovered = await post(http)
    assert recovered.status_code == 200
    assert router.audio.call_count == 2
