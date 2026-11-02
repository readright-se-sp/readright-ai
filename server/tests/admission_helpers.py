"""Shared builders for admission, readiness and last-request tests."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from app.admission import WorkerStats
from conftest import API_KEY, fake_analysis, valid_body

from app.config import Settings
from app.main import create_app

AUTH = {"X-API-Key": API_KEY}


def fast_settings(**overrides: Any) -> Settings:
    """Settings whose time estimates are tiny, so only the scenario under test can reject."""
    values: dict[str, Any] = {
        "stage_estimate_download_seconds": 0,
        "stage_estimate_upload_seconds": 0,
        "stage_estimate_lane_seconds": 0.01,
        "audio_worker_url": "http://audio.test:8001",
        "video_worker_url": "http://video.test:8002",
    }
    values.update(overrides)
    return Settings(_env_file=None, api_key=API_KEY, **values)  # type: ignore[call-arg]


def new_body(**overrides: Any) -> dict[str, Any]:
    return valid_body(request_id=str(uuid.uuid4()), **overrides)


class FakeStats:
    """A `worker_stats` source that records calls and can be told what to report or raise."""

    def __init__(
        self,
        audio: tuple[int, float | None] = (0, None),
        video: tuple[int, float | None] = (0, None),
    ) -> None:
        self.by_lane = {"audio": audio, "video": video}
        self.calls: list[str] = []
        self.error: Exception | None = None

    async def __call__(self, lane: str) -> WorkerStats:
        self.calls.append(lane)
        if self.error is not None:
            raise self.error
        queue, avg = self.by_lane[lane]
        return WorkerStats(queue_length=queue, avg_processing_seconds=avg)


class CountingProcessor:
    """Fake processor; `gate` (if set) blocks every call until set."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.calls = 0
        self.error: Exception | None = None

    async def __call__(self, body, settings):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return fake_analysis(body, settings)


async def wait_until(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@asynccontextmanager
async def running(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def build_app(settings, redis, processor, worker_stats, dependency_checks=None):
    return create_app(
        settings,
        redis=redis,
        processor=processor,
        worker_stats=worker_stats,
        dependency_checks={} if dependency_checks is None else dependency_checks,
    )
