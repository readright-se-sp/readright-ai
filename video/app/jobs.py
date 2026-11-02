import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from .analyzer import Analyzer, JobCancelled, JobContext, VideoFlags
from .config import Settings
from .errors import LaneError
from .models import ErrorCode, VideoJob

logger = logging.getLogger(__name__)

IsDisconnected = Callable[[], Awaitable[bool]]


def _now_ms() -> int:
    return int(time.time() * 1000)


class JobRunner:
    def __init__(self, settings: Settings, analyzer: Analyzer) -> None:
        self._settings = settings
        self._analyzer = analyzer
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._waiting = 0
        self._durations: deque[float] = deque(maxlen=settings.stats_window)

    @property
    def queue_length(self) -> int:
        return self._waiting

    @property
    def avg_processing_seconds(self) -> float | None:
        if not self._durations:
            return None
        return sum(self._durations) / len(self._durations)

    async def _poll(self, is_disconnected: IsDisconnected) -> None:
        """Returns once the client is gone. A failing check counts as gone."""
        try:
            while not await is_disconnected():
                await asyncio.sleep(self._settings.disconnect_poll_seconds)
        except Exception:
            logger.exception("disconnect check failed; treating the client as gone")

    async def _acquire_slot(self, is_disconnected: IsDisconnected) -> None:
        """Wait for a slot; raise JobCancelled if the client leaves first."""
        acquire = asyncio.ensure_future(self._semaphore.acquire())
        watcher = asyncio.ensure_future(self._poll(is_disconnected))
        try:
            await asyncio.wait({acquire, watcher}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            acquire.cancel()
            watcher.cancel()
            if acquire.done() and not acquire.cancelled():
                self._semaphore.release()
            raise
        if not watcher.done():
            watcher.cancel()
            return
        acquire.cancel()
        await asyncio.gather(acquire, return_exceptions=True)
        if not acquire.cancelled():
            self._semaphore.release()
        raise JobCancelled()

    async def run(self, job: VideoJob, is_disconnected: IsDisconnected) -> VideoFlags:
        ctx = JobContext(job_id=job.job_id, deadline_ms=job.deadline)
        self._waiting += 1
        try:
            await self._acquire_slot(is_disconnected)
        finally:
            self._waiting -= 1
        try:
            if _now_ms() >= job.deadline:
                raise LaneError(ErrorCode.DEADLINE_EXCEEDED, "Deadline exceeded.")
            return await self._analyze(job, ctx, is_disconnected)
        finally:
            self._semaphore.release()

    async def _analyze(
        self, job: VideoJob, ctx: JobContext, is_disconnected: IsDisconnected
    ) -> VideoFlags:
        async def watch() -> None:
            await self._poll(is_disconnected)
            ctx.cancel_event.set()

        start = time.perf_counter()
        watcher = asyncio.ensure_future(watch())
        work = asyncio.ensure_future(
            asyncio.to_thread(self._analyzer.analyze, Path(job.webm_path), ctx)
        )
        try:
            flags = await asyncio.shield(work)
        except asyncio.CancelledError:
            # Keep the slot until the thread ends; ask it to stop meanwhile.
            ctx.cancel_event.set()
            while not work.done():
                await asyncio.wait({work})
            if not work.cancelled():
                work.exception()  # retrieve, so nothing is logged as never retrieved
            raise
        finally:
            watcher.cancel()
        if ctx.cancel_event.is_set():
            raise JobCancelled()
        self._durations.append(time.perf_counter() - start)
        return flags
