import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .deadline import Deadline
from .errors import MAIError
from .models import ErrorCode

logger = logging.getLogger(__name__)

Lane = Literal["audio", "video"]


class WorkerStats(BaseModel):
    """Body of a worker's `GET /stats` (see contracts/worker-api.md)."""

    queue_length: int = Field(ge=0)
    avg_processing_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None


WorkerStatsSource = Callable[[Lane], Awaitable[WorkerStats]]


def estimate_finish_seconds(
    audio: WorkerStats, video: WorkerStats, in_flight_others: int, settings: Settings
) -> float:
    """Download + slowest lane (queue ahead plus this request) + upload, in seconds."""
    lanes = []
    for stats in (audio, video):
        queue = max(stats.queue_length, in_flight_others)  # requests still downloading count
        average = stats.avg_processing_seconds
        if average is None:
            average = settings.stage_estimate_lane_seconds
        lanes.append((queue + 1) * average)
    return (
        settings.stage_estimate_download_seconds
        + max(lanes)
        + settings.stage_estimate_upload_seconds
    )


def retry_after_seconds(estimate: float, remaining: float) -> int:
    return max(1, math.ceil(estimate - remaining))


async def fetch_worker_stats(
    base_url: str, settings: Settings, client: httpx.AsyncClient | None = None
) -> WorkerStats:
    """GET `<base_url>/stats`; raises on any failure (never echoes the worker's body)."""
    timeout = httpx.Timeout(settings.dependency_timeout_seconds)
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as own:
            return await fetch_worker_stats(base_url, settings, own)
    response = await client.get(f"{base_url.rstrip('/')}/stats", timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"stats returned status {response.status_code}")
    try:
        return WorkerStats.model_validate_json(response.content)
    except ValidationError:
        raise RuntimeError("stats body is invalid") from None


def cached_worker_stats_source(settings: Settings) -> WorkerStatsSource:
    """Default source: HTTP fetch per lane, cached for `stats_cache_seconds`, single-flight."""
    urls: dict[Lane, str] = {
        "audio": settings.audio_worker_url,
        "video": settings.video_worker_url,
    }
    cached: dict[Lane, tuple[float, WorkerStats]] = {}
    pending: dict[Lane, asyncio.Task[WorkerStats]] = {}

    async def fetch(lane: Lane) -> WorkerStats:
        stats = await fetch_worker_stats(urls[lane], settings)
        cached[lane] = (time.monotonic() + settings.stats_cache_seconds, stats)
        return stats

    def done(lane: Lane, task: asyncio.Task[WorkerStats]) -> None:
        pending.pop(lane, None)
        if not task.cancelled():
            task.exception()  # mark retrieved even if every waiter was cancelled

    async def source(lane: Lane) -> WorkerStats:
        entry = cached.get(lane)
        if entry is not None and time.monotonic() < entry[0]:
            return entry[1]
        task = pending.get(lane)
        if task is None:
            task = asyncio.create_task(fetch(lane))
            pending[lane] = task
            task.add_done_callback(lambda t: done(lane, t))
        return await asyncio.shield(task)  # a cancelled waiter must not cancel the shared fetch

    return source


class Admission:
    """Per-process in-flight counter plus the deadline estimate check."""

    def __init__(self, settings: Settings, worker_stats: WorkerStatsSource) -> None:
        self._settings = settings
        self._worker_stats = worker_stats
        self.in_flight = 0

    def acquire(self) -> None:
        """Take an in-flight slot or raise 429. No await between the check and the increment."""
        if self.in_flight >= self._settings.max_in_flight:
            logger.warning(
                "admission rejected",
                extra={
                    "reason": "in_flight_cap",
                    "in_flight": self.in_flight,
                    "max_in_flight": self._settings.max_in_flight,
                },
            )
            raise MAIError(ErrorCode.RATE_LIMITED, "The server is at capacity.")
        self.in_flight += 1

    def release(self) -> None:
        self.in_flight -= 1

    async def check_deadline(self, deadline: Deadline) -> None:
        """Raise 503 if worker stats are unavailable, 429 if the estimate exceeds the deadline."""
        limit = min(self._settings.dependency_timeout_seconds, deadline.remaining())
        try:
            async with asyncio.timeout(limit):  # also bounds injected sources
                audio, video = await asyncio.gather(
                    self._worker_stats("audio"), self._worker_stats("video")
                )
        except Exception as exc:
            logger.warning("worker stats unavailable", extra={"error": type(exc).__name__})
            raise MAIError(
                ErrorCode.SERVICE_UNAVAILABLE, "Worker status could not be determined."
            ) from None
        remaining = deadline.remaining()  # read after the fetch so the decision is not stale
        estimate = estimate_finish_seconds(audio, video, self.in_flight - 1, self._settings)
        if estimate > remaining:
            logger.warning(
                "admission rejected",
                extra={
                    "reason": "deadline",
                    "estimate_seconds": estimate,
                    "remaining_seconds": remaining,
                },
            )
            raise MAIError(
                ErrorCode.RATE_LIMITED,
                "The request cannot finish before its deadline.",
                retry_after=retry_after_seconds(estimate, remaining),
            )
