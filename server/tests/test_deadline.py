"""Phase 3: the request deadline (criteria 3-6)."""

import asyncio

import pytest
from conftest import API_KEY, fake_analysis, valid_body

from app.config import Settings
from app.deadline import Deadline, deadline_var
from app.errors import MAIError
from app.models import ErrorCode

pytestmark = pytest.mark.anyio

KEY = "mai:req:3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"


@pytest.fixture
def settings() -> Settings:
    """Overrides the conftest fixture: a short deadline."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        api_key=API_KEY,
        request_deadline_seconds=0.3,
        in_progress_ttl_seconds=130,
    )


class Slow:
    """Fake processor that sleeps for a long time, or fails/succeeds on demand."""

    def __init__(self, *, sleep: float = 10.0, error: BaseException | None = None):
        self.calls = 0
        self.cancelled = 0
        self.sleep = sleep
        self.error = error
        self.seen_deadline: Deadline | None = None

    async def __call__(self, body, settings):
        self.calls += 1
        self.seen_deadline = deadline_var.get()
        if self.error is not None:
            raise self.error
        try:
            await asyncio.sleep(self.sleep)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return fake_analysis(body, settings)


async def post(client, auth):
    return await client.post("/v1/analyses", json=valid_body(), headers=auth)


# 3. Deadline class ------------------------------------------------------------------------------


def test_c3_deadline_remaining_and_expired_follow_the_clock():
    now = [100.0]
    deadline = Deadline(5.0, clock=lambda: now[0])
    assert deadline.remaining() == pytest.approx(5.0)
    assert deadline.expired is False
    now[0] = 103.0
    assert deadline.remaining() == pytest.approx(2.0)
    assert deadline.expired is False


def test_c3_deadline_remaining_is_never_negative():
    now = [0.0]
    deadline = Deadline(1.0, clock=lambda: now[0])
    now[0] = 50.0
    assert deadline.remaining() == 0.0
    assert deadline.expired is True


def test_c3_deadline_uses_monotonic_clock_by_default():
    deadline = Deadline(60.0)
    assert 59.0 < deadline.remaining() <= 60.0


def test_c3_deadline_var_defaults_to_none():
    assert deadline_var.get() is None


# 4. the route sets the deadline -----------------------------------------------------------------


async def test_c4_route_sets_deadline_var_from_settings(app, async_client, auth):
    processor = app.state.processor = Slow(sleep=0)
    assert (await post(async_client, auth)).status_code == 200
    assert isinstance(processor.seen_deadline, Deadline)
    assert 0 < processor.seen_deadline.remaining() <= 0.3


# 5. timeout behavior ----------------------------------------------------------------------------


async def test_c5_deadline_gives_504_timeout_and_cancels_processor(app, async_client, auth):
    processor = app.state.processor = Slow()
    response = await post(async_client, auth)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "TIMEOUT"
    assert processor.cancelled == 1


async def test_c5_timeout_releases_idempotency_record_so_retry_is_processed(
    app, async_client, auth, redis
):
    processor = app.state.processor = Slow()
    assert (await post(async_client, auth)).status_code == 504
    assert await redis.exists(KEY) == 0

    processor.sleep = 0
    retry = await post(async_client, auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers
    assert processor.calls == 2


async def test_c5_timeout_error_not_from_deadline_is_not_504(app, async_client, auth):
    app.state.processor = Slow(error=TimeoutError("some inner timeout"))
    response = await post(async_client, auth)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_c5_mai_error_timeout_from_processor_is_504(app, async_client, auth):
    app.state.processor = Slow(error=MAIError(ErrorCode.TIMEOUT, "slow"))
    response = await post(async_client, auth)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "TIMEOUT"


# 6. default test setup -------------------------------------------------------------------------


async def test_c6_deadline_applies_with_default_test_setup(app, async_client, auth):
    app.state.processor = Slow()
    response = await post(async_client, auth)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "TIMEOUT"
