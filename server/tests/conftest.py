import logging
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import AnalysisRequest, AnalysisResponse, Miscue, MiscueType

API_KEY = "test-key"
SIGNATURE = "SUPERSECRETSIGNATURE123"


def valid_body(**overrides: Any) -> dict[str, Any]:
    """The contract's example request."""
    body: dict[str, Any] = {
        "request_id": "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11",
        "video_url": (
            "https://bucket.s3.ap-southeast-1.amazonaws.com/recordings/abc.webm"
            f"?X-Amz-Signature={SIGNATURE}"
        ),
        "passage_title": "Pam's Cat",
        "passage_content": (
            "Pam has a cat. It is on the bed. It can nap. It can sit. “Oh no!” says Pam. "
            "“The cat fell off the bed!” Is the cat sad? No. It is on the mat."
        ),
    }
    body.update(overrides)
    return body


def fake_analysis(body: AnalysisRequest, settings: Settings | None = None) -> AnalysisResponse:
    """A fixed, valid result for fake processors; no pipeline involved."""
    prefix = f"analyses/{body.request_id}/miscues/"
    return AnalysisResponse(
        request_id=body.request_id,
        reading_score=85.0,
        wpm=60.0,
        is_word_by_word=False,
        is_disregard_punctuation=True,
        is_monotonous=False,
        is_lack_expression=False,
        is_hardly_audible=False,
        is_finger_pointing=False,
        miscues=[
            Miscue(
                actual_phrase="Pam",
                spoken_phrase="Pa",
                actual_phoneme=["P", "a", "m"],
                spoken_phoneme=["P", "a"],
                read_start=500,
                read_end=1000,
                audio_object_key=f"{prefix}miscue-0001.wav",
                miscue_type=MiscueType.SUBSTITUTION,
            )
        ],
    )


async def fake_processor(body: AnalysisRequest, settings: Settings) -> AnalysisResponse:
    """Default processor injected into the `app` fixture so client tests skip the pipeline."""
    return fake_analysis(body, settings)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's MAI_* shell variables from leaking into tests."""
    for name in list(os.environ):
        if name.upper().startswith("MAI_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: ignore a local server/.env.
    return Settings(_env_file=None, api_key=API_KEY)  # type: ignore[call-arg]


@pytest.fixture
def log_records(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog that also works after `create_app` has replaced the root handlers."""
    root = logging.getLogger()
    added = caplog.handler not in root.handlers
    if added:
        root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        if added:
            root.removeHandler(caplog.handler)


@pytest.fixture
def anyio_backend() -> str:
    """Run `@pytest.mark.anyio` tests on asyncio only."""
    return "asyncio"


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def redis(redis_server: fakeredis.FakeServer) -> fakeredis.aioredis.FakeRedis:
    """A fresh in-memory Redis per test. The same client is injected into the app."""
    return fakeredis.aioredis.FakeRedis(server=redis_server, decode_responses=True)


@pytest.fixture
def app(settings: Settings, redis):
    return create_app(settings, redis=redis, processor=fake_processor)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # raise_server_exceptions=False so unhandled errors surface as real 500 responses.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
async def async_client(app) -> AsyncIterator[httpx.AsyncClient]:
    """Async client for concurrency tests. Runs the app lifespan like TestClient does."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
