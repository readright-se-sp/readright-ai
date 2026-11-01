"""Phase 2: idempotency of POST /v1/analyses, backed by (fake) Redis.

The tests use the public surface only: HTTP, `app.state.processor` (swapped for fakes),
and direct inspection of the `mai:req:<request_id>` key.
"""

import asyncio
import json
import logging

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
from conftest import fake_analysis, valid_body
from pydantic import ValidationError

from app.config import Settings
from app.errors import MAIError
from app.idempotency import IdempotencyStore, request_fingerprint
from app.main import create_app
from app.models import AnalysisRequest, ErrorCode

pytestmark = pytest.mark.anyio

REQUEST_ID = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"
KEY = f"mai:req:{REQUEST_ID}"
URL_PASSWORD = "s3cretpw"
WEEK = 604800


class Processor:
    """Counting fake for `app.state.processor`. Hooks let a test act while it runs."""

    def __init__(self, *, error=None, block_first=False, during=None):
        self.calls = 0
        self.error = error
        self.during = during  # async callable run in the middle of processing
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._block_first = block_first

    async def __call__(self, body: AnalysisRequest, settings: Settings):
        self.calls += 1
        first = self.calls == 1
        self.started.set()
        if self.during is not None:
            await self.during()
        if first and self._block_first:
            await asyncio.wait_for(self.release.wait(), timeout=10)
        if self.error is not None:
            raise self.error
        return fake_analysis(body, settings)


async def post(client, auth, **overrides):
    return await client.post("/v1/analyses", json=valid_body(**overrides), headers=auth)


async def stored(redis) -> dict:
    raw = await redis.get(KEY)
    assert raw is not None, "expected a Redis record"
    return json.loads(raw)


def error_code(response) -> str:
    return response.json()["error"]["code"]


# 1. first request -------------------------------------------------------------------------------


async def test_first_request_processes_once_and_stores_done(app, async_client, auth, redis):
    processor = app.state.processor = Processor()
    response = await post(async_client, auth)
    assert response.status_code == 200
    assert "X-MAI-Replayed" not in response.headers
    assert processor.calls == 1
    record = await stored(redis)
    assert record["state"] == "done"
    assert record["token"]
    ttl = await redis.ttl(KEY)
    assert WEEK - 60 < ttl <= WEEK


# 2. replay --------------------------------------------------------------------------------------


async def test_repeat_replays_identical_body_without_reprocessing(app, async_client, auth):
    processor = app.state.processor = Processor()
    first = await post(async_client, auth)
    second = await post(async_client, auth)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.headers["X-MAI-Replayed"] == "true"
    assert processor.calls == 1


# 3. concurrent duplicates -----------------------------------------------------------------------


async def test_concurrent_duplicates_one_wins_rest_409(app, async_client, auth):
    processor = app.state.processor = Processor(block_first=True)
    tasks = [asyncio.create_task(post(async_client, auth)) for _ in range(5)]
    async with asyncio.timeout(10):
        await processor.started.wait()
        # The four losers answer while the winner is still blocked.
        while sum(t.done() for t in tasks) < 4:
            await asyncio.sleep(0)
    processor.release.set()
    async with asyncio.timeout(10):
        responses = await asyncio.gather(*tasks)

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409, 409, 409, 409]
    for r in responses:
        if r.status_code == 409:
            assert error_code(r) == "REQUEST_IN_PROGRESS"
    assert processor.calls == 1


# 4. in-progress lease ---------------------------------------------------------------------------


async def test_in_progress_key_has_lease_ttl(app, async_client, auth, redis):
    seen = {}

    async def inspect():
        seen["record"] = await stored(redis)
        seen["ttl"] = await redis.ttl(KEY)

    app.state.processor = Processor(during=inspect)
    assert (await post(async_client, auth)).status_code == 200
    assert seen["record"]["state"] == "in_progress"
    assert 120 < seen["ttl"] <= 130


async def test_expired_lease_lets_retry_process_and_stale_finish_is_harmless(
    app, async_client, auth, redis
):
    processor = app.state.processor = Processor(block_first=True)
    first = asyncio.create_task(post(async_client, auth))
    async with asyncio.timeout(10):
        await processor.started.wait()
    await redis.delete(KEY)  # lease expired

    retry = await post(async_client, auth)
    assert retry.status_code == 200
    assert processor.calls == 2
    done = await stored(redis)
    assert done["state"] == "done"

    processor.release.set()
    async with asyncio.timeout(10):
        await first
    # The stale first request must not have overwritten the newer record.
    assert await stored(redis) == done


# 5. failures are not cached ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (MAIError(ErrorCode.VIDEO_URL_EXPIRED, "expired"), 422, "VIDEO_URL_EXPIRED"),
        (RuntimeError("boom"), 500, "INTERNAL_ERROR"),
    ],
    ids=["mai_error", "bare_exception"],
)
async def test_failure_releases_key_and_retry_succeeds(
    app, async_client, auth, redis, error, status, code
):
    processor = app.state.processor = Processor(error=error)
    response = await post(async_client, auth)
    assert response.status_code == status
    assert error_code(response) == code
    assert await redis.exists(KEY) == 0

    processor.error = None
    retry = await post(async_client, auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers
    assert processor.calls == 2
    assert (await stored(redis))["state"] == "done"


# 6. stale token ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (None, 200),
        (MAIError(ErrorCode.VIDEO_URL_EXPIRED, "expired"), 422),
        (RuntimeError("x"), 500),
    ],
    ids=["succeeds", "mai_error", "bare_exception"],
)
async def test_stale_request_does_not_touch_newer_record(
    app, async_client, auth, redis, error, status
):
    newer = json.dumps(
        {"state": "in_progress", "token": "other-token", "body_hash": "x", "claimed_at": 1}
    )

    async def take_over():
        # Lease expired and another request claimed the id while we were running.
        await redis.set(KEY, newer, ex=100)

    app.state.processor = Processor(error=error, during=take_over)
    response = await post(async_client, auth)
    assert response.status_code == status
    assert await redis.get(KEY) == newer
    assert 0 < await redis.ttl(KEY) <= 100


# 7. body mismatch -------------------------------------------------------------------------------


async def test_different_passage_with_same_request_id_is_400(app, async_client, auth, redis):
    processor = app.state.processor = Processor()
    assert (await post(async_client, auth)).status_code == 200
    before = await redis.get(KEY)

    response = await post(async_client, auth, passage_content="A totally different passage.")
    assert response.status_code == 400
    assert error_code(response) == "INVALID_REQUEST"
    assert processor.calls == 1
    assert await redis.get(KEY) == before


async def test_different_passage_while_in_progress_is_400_not_409(app, async_client, auth):
    processor = app.state.processor = Processor(block_first=True)
    first = asyncio.create_task(post(async_client, auth))
    async with asyncio.timeout(10):
        await processor.started.wait()
    response = await post(async_client, auth, passage_content="Another passage.")
    assert response.status_code == 400
    assert error_code(response) == "INVALID_REQUEST"
    processor.release.set()
    assert (await first).status_code == 200


async def test_different_presigned_query_is_still_a_replay(app, async_client, auth):
    processor = app.state.processor = Processor()
    base = "https://bucket.s3.ap-southeast-1.amazonaws.com/recordings/abc.webm"
    first = await post(async_client, auth, video_url=f"{base}?X-Amz-Signature=AAAA")
    second = await post(async_client, auth, video_url=f"{base}?X-Amz-Signature=BBBB&X-Amz-Date=1")
    assert second.status_code == 200
    assert second.headers["X-MAI-Replayed"] == "true"
    assert second.json() == first.json()
    assert processor.calls == 1


async def test_different_video_path_is_a_mismatch(app, async_client, auth):
    app.state.processor = Processor()
    base = "https://bucket.s3.ap-southeast-1.amazonaws.com"
    assert (await post(async_client, auth, video_url=f"{base}/a.webm?s=1")).status_code == 200
    response = await post(async_client, auth, video_url=f"{base}/b.webm?s=1")
    assert response.status_code == 400
    assert error_code(response) == "INVALID_REQUEST"


# 8. Redis down ----------------------------------------------------------------------------------


async def test_redis_down_gives_503_with_retry_after(app, async_client, auth, redis_server):
    processor = app.state.processor = Processor()
    redis_server.connected = False
    response = await post(async_client, auth)
    assert response.status_code == 503
    assert error_code(response) == "SERVICE_UNAVAILABLE"
    assert "Retry-After" in response.headers
    assert processor.calls == 0


async def test_redis_down_fails_readiness(async_client, redis_server):
    assert (await async_client.get("/health/ready")).json()["checks"] == {"redis": True}
    redis_server.connected = False
    response = await async_client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["details"] == {"failed_checks": ["redis"]}


# 9. Redis fails only after the work -------------------------------------------------------------


async def test_redis_failing_in_complete_still_returns_200(
    app, async_client, auth, redis, redis_server
):
    async def disconnect():
        redis_server.connected = False

    processor = app.state.processor = Processor(during=disconnect)
    response = await post(async_client, auth)
    assert response.status_code == 200
    assert response.json()["request_id"] == REQUEST_ID

    # The result was never stored as done, so after recovery a retry reprocesses.
    redis_server.connected = True
    raw = await redis.get(KEY)
    assert raw is None or json.loads(raw)["state"] != "done"
    await redis.delete(KEY)  # the in-progress lease would otherwise expire on its own
    assert (await post(async_client, auth)).status_code == 200
    assert processor.calls == 2


async def test_redis_failing_in_release_keeps_original_error(app, async_client, auth, redis_server):
    async def disconnect():
        redis_server.connected = False

    app.state.processor = Processor(
        error=MAIError(ErrorCode.VIDEO_URL_EXPIRED, "expired"), during=disconnect
    )
    response = await post(async_client, auth)
    assert response.status_code == 422
    assert error_code(response) == "VIDEO_URL_EXPIRED"


# 10. settings and secrecy -----------------------------------------------------------------------


def test_redis_settings_defaults():
    settings = Settings(_env_file=None, api_key="k")  # type: ignore[call-arg]
    assert settings.redis_url.get_secret_value() == "redis://localhost:6379/0"
    assert settings.in_progress_ttl_seconds == 130
    assert settings.result_ttl_seconds == WEEK
    assert settings.redis_timeout_seconds == 2.0


def test_redis_settings_from_env(monkeypatch):
    monkeypatch.setenv("MAI_API_KEY", "k")
    monkeypatch.setenv("MAI_REDIS_URL", "redis://example:6380/2")
    monkeypatch.setenv("MAI_IN_PROGRESS_TTL_SECONDS", "200")
    monkeypatch.setenv("MAI_RESULT_TTL_SECONDS", "60")
    monkeypatch.setenv("MAI_REDIS_TIMEOUT_SECONDS", "0.5")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.redis_url.get_secret_value() == "redis://example:6380/2"
    assert settings.in_progress_ttl_seconds == 200
    assert settings.result_ttl_seconds == 60
    assert settings.redis_timeout_seconds == 0.5


@pytest.mark.parametrize(("ttl", "deadline"), [(115, 115.0), (100, 115.0), (60, 60.0)])
def test_in_progress_ttl_must_exceed_deadline(ttl, deadline):
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            api_key="k",
            in_progress_ttl_seconds=ttl,
            request_deadline_seconds=deadline,
        )


def test_in_progress_ttl_just_above_deadline_is_valid():
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, api_key="k", in_progress_ttl_seconds=116, request_deadline_seconds=115.0
    )
    assert settings.in_progress_ttl_seconds == 116


async def test_redis_url_password_never_leaks(auth, caplog):
    url = f"redis://:{URL_PASSWORD}@localhost:6379/0"
    settings = Settings(_env_file=None, api_key="test-key", redis_url=url)  # type: ignore[call-arg]
    assert URL_PASSWORD not in repr(settings)
    assert URL_PASSWORD not in str(settings)

    server = fakeredis.FakeServer()
    app = create_app(settings, redis=fakeredis.aioredis.FakeRedis(server=server))
    # configure_logging() replaces the root handlers, so attach caplog's directly.
    root = logging.getLogger()
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    texts = []
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                server.connected = False
                texts.append((await post(http, auth)).text)
                texts.append((await http.get("/health/ready")).text)
                server.connected = True
                texts.append((await post(http, auth)).text)
    finally:
        root.removeHandler(caplog.handler)

    logged = [caplog.text] + [str(r.__dict__) for r in caplog.records]
    assert URL_PASSWORD not in "\n".join(texts + logged)


# 11. round 2: atomic claim ----------------------------------------------------------------------


def _record(body: AnalysisRequest, settings: Settings, state: str, **extra) -> str:
    record = {
        "state": state,
        "token": "seeded-token",
        "body_hash": request_fingerprint(body),
        "claimed_at": 1,
    }
    if state == "done":
        record["response"] = fake_analysis(body, settings).model_dump(mode="json")
    record.update(extra)
    return json.dumps(record)


def _store(redis) -> IdempotencyStore:
    return IdempotencyStore(redis, 130, WEEK)


async def test_claim_lost_race_to_in_progress_raises_409(redis, settings):
    body = AnalysisRequest(**valid_body())
    seeded = _record(body, settings, "in_progress")
    await redis.set(KEY, seeded, ex=100)
    with pytest.raises(MAIError) as exc:
        await _store(redis).claim(body)
    assert exc.value.code == ErrorCode.REQUEST_IN_PROGRESS
    assert await redis.get(KEY) == seeded


async def test_claim_lost_race_to_done_returns_stored_response(redis, settings):
    body = AnalysisRequest(**valid_body())
    seeded = _record(body, settings, "done")
    await redis.set(KEY, seeded, ex=100)
    result = await _store(redis).claim(body)
    assert result == fake_analysis(body, settings)
    assert await redis.get(KEY) == seeded


async def test_claim_lost_race_to_different_body_raises_400(redis, settings):
    body = AnalysisRequest(**valid_body())
    seeded = _record(body, settings, "in_progress", body_hash="different")
    await redis.set(KEY, seeded, ex=100)
    with pytest.raises(MAIError) as exc:
        await _store(redis).claim(body)
    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert await redis.get(KEY) == seeded


async def test_concurrent_duplicates_race_on_claim_when_lookup_misses(
    app, async_client, auth, monkeypatch
):
    processor = app.state.processor = Processor(block_first=True)

    async def miss(body):
        return None

    monkeypatch.setattr(app.state.idempotency, "lookup", miss)
    tasks = [asyncio.create_task(post(async_client, auth)) for _ in range(5)]
    async with asyncio.timeout(10):
        await processor.started.wait()
        while sum(t.done() for t in tasks) < 4:
            await asyncio.sleep(0)
    processor.release.set()
    async with asyncio.timeout(10):
        responses = await asyncio.gather(*tasks)
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409, 409]
    assert processor.calls == 1


# 12. round 2: cancellation ----------------------------------------------------------------------


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


async def test_cancel_during_processing_releases_key(app, async_client, auth, redis):
    processor = app.state.processor = Processor(block_first=True)
    task = asyncio.create_task(post(async_client, auth))
    async with asyncio.timeout(10):
        await processor.started.wait()
    assert await redis.exists(KEY) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _settle()
    assert await redis.exists(KEY) == 0

    retry = await post(async_client, auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers
    assert processor.calls == 2


async def test_cancel_during_complete_leaves_no_stuck_lease(
    app, async_client, auth, redis, monkeypatch
):
    processor = app.state.processor = Processor()
    entered = asyncio.Event()
    gate = asyncio.Event()
    real_eval = redis.eval

    async def slow_eval(*args, **kwargs):
        entered.set()
        await gate.wait()
        return await real_eval(*args, **kwargs)

    monkeypatch.setattr(redis, "eval", slow_eval)  # the only eval before any release is complete
    task = asyncio.create_task(post(async_client, auth))
    async with asyncio.timeout(10):
        await entered.wait()
    task.cancel()
    gate.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await _settle()

    raw = await redis.get(KEY)
    assert raw is None or json.loads(raw)["state"] == "done"

    monkeypatch.setattr(redis, "eval", real_eval)
    retry = await post(async_client, auth)
    assert retry.status_code == 200
    assert processor.calls <= 2


# 13. round 2: settings must be positive ---------------------------------------------------------


@pytest.mark.parametrize("field", ["result_ttl_seconds", "redis_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_positive_settings_reject_zero_and_negative(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="k", **{field: value})  # type: ignore[call-arg]


@pytest.mark.parametrize("value", [0, -1])
def test_in_progress_ttl_rejects_zero_and_negative(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="k", in_progress_ttl_seconds=value)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "value"),
    [("MAI_RESULT_TTL_SECONDS", "0"), ("MAI_REDIS_TIMEOUT_SECONDS", "0")],
)
def test_positive_settings_reject_zero_from_env(monkeypatch, name, value):
    monkeypatch.setenv("MAI_API_KEY", "k")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


# 14. round 2: output_key_prefix trailing slash --------------------------------------------------


async def test_trailing_slash_on_output_key_prefix_is_a_replay(app, async_client, auth):
    processor = app.state.processor = Processor()
    first = await post(async_client, auth, output_key_prefix="custom/path")
    assert first.status_code == 200
    second = await post(async_client, auth, output_key_prefix="custom/path/")
    assert second.status_code == 200
    assert second.headers["X-MAI-Replayed"] == "true"
    assert processor.calls == 1


async def test_different_output_key_prefix_is_400(app, async_client, auth):
    app.state.processor = Processor()
    assert (await post(async_client, auth, output_key_prefix="custom/path")).status_code == 200
    response = await post(async_client, auth, output_key_prefix="other/path/")
    assert response.status_code == 400
    assert error_code(response) == "INVALID_REQUEST"
