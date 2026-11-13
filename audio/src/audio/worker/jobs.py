"""Slot control for the worker: one semaphore, queue length, rolling average, disconnect polling."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from audio.config import AudioSettings
from audio.worker.schemas import ErrorCode, LaneError

logger = logging.getLogger(__name__)

IsDisconnected = Callable[[], Awaitable[bool]]
T = TypeVar("T")


class JobCancelled(Exception):
    """The client closed the connection before the job finished."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _deadline_error() -> LaneError:
    return LaneError(ErrorCode.DEADLINE_EXCEEDED, "Deadline exceeded.")


class JobRunner:
    def __init__(self, settings: AudioSettings) -> None:
        self._poll_s = settings.WORKER_DISCONNECT_POLL_S
        self._semaphore = asyncio.Semaphore(settings.WORKER_MAX_CONCURRENT_JOBS)
        self._waiting = 0
        self._durations: deque[float] = deque(maxlen=settings.WORKER_STATS_WINDOW)

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
                await asyncio.sleep(self._poll_s)
        except Exception:
            logger.exception("disconnect check failed; treating the client as gone")

    async def _acquire_slot(self, is_disconnected: IsDisconnected, deadline_ms: int) -> None:
        """Wait for a slot. JobCancelled if the client leaves first, 504 if the deadline passes."""
        acquire = asyncio.ensure_future(self._semaphore.acquire())
        watcher = asyncio.ensure_future(self._poll(is_disconnected))
        timeout = max(0.0, (deadline_ms - _now_ms()) / 1000)
        try:
            await asyncio.wait(
                {acquire, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            acquire.cancel()
            watcher.cancel()
            if acquire.done() and not acquire.cancelled():
                self._semaphore.release()
            raise
        watcher.cancel()
        if acquire.done() and not acquire.cancelled():
            return  # got the slot (a client that left at the same moment is caught after the run)
        acquire.cancel()
        await asyncio.gather(acquire, return_exceptions=True)
        if not acquire.cancelled():
            self._semaphore.release()
        if watcher.done() and not watcher.cancelled():
            raise JobCancelled()
        raise _deadline_error()

    async def run(
        self, deadline_ms: int, is_disconnected: IsDisconnected, work: Callable[[], T]
    ) -> T:
        """Waits for a slot, then runs ``work`` in a thread. 504 if the deadline passes while
        waiting or while running (the thread can't be interrupted, so its result is dropped)."""
        self._waiting += 1
        try:
            await self._acquire_slot(is_disconnected, deadline_ms)
        finally:
            self._waiting -= 1
        try:
            if _now_ms() >= deadline_ms:
                raise _deadline_error()
            start = time.perf_counter()
            result = await self._run_in_thread(is_disconnected, work)
            self._durations.append(time.perf_counter() - start)
            if _now_ms() >= deadline_ms:
                raise _deadline_error()
            return result
        finally:
            self._semaphore.release()

    async def _run_in_thread(self, is_disconnected: IsDisconnected, work: Callable[[], T]) -> T:
        gone = asyncio.Event()

        async def watch() -> None:
            await self._poll(is_disconnected)
            gone.set()

        watcher = asyncio.ensure_future(watch())
        thread = asyncio.ensure_future(asyncio.to_thread(work))
        try:
            result = await asyncio.shield(thread)
        except asyncio.CancelledError:
            # Keep the slot until the thread ends: the GPU is still busy.
            while not thread.done():
                await asyncio.wait({thread})
            if not thread.cancelled():
                thread.exception()  # retrieve, so nothing is logged as never retrieved
            raise
        finally:
            watcher.cancel()
        if gone.is_set():
            raise JobCancelled()  # the thread finished; the result is dropped
        return result
