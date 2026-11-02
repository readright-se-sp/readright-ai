"""Shared test helpers: a configurable fake analyzer and request/client builders."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.analyzer import JobCancelled, JobContext, VideoFlags
from app.config import Settings
from app.main import create_app
from app.models import VideoJob


def now_ms() -> int:
    return int(time.time() * 1000)


def future_deadline_ms() -> int:
    return now_ms() + 60_000


def make_settings(**overrides: Any) -> Settings:
    """Settings that ignore any local .env and poll for disconnects quickly."""
    values: dict[str, Any] = {"disconnect_poll_seconds": 0.05}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def valid_body(webm_path: Path | str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "job_id": "job-1",
        "deadline": future_deadline_ms(),
        "webm_path": str(webm_path),
    }
    body.update(overrides)
    return body


def make_job(webm_path: Path | str, job_id: str = "job-1", deadline: int | None = None) -> VideoJob:
    return VideoJob(
        job_id=job_id,
        deadline=future_deadline_ms() if deadline is None else deadline,
        webm_path=str(webm_path),
    )


async def never_disconnected() -> bool:
    return False


class FakeAnalyzer:
    """Analyzer double with configurable load and analyze behavior.

    - `ready`: initial readiness; a successful `load()` sets it True.
    - `load_gate`: if given, `load()` blocks until it is set.
    - `load_error`: if given, `load()` raises it (after the gate).
    - `flags`: what `analyze` returns.
    - `analyze_error`: if given, `analyze` raises it (after delay and gate).
    - `analyze_gate`: if given, `analyze` blocks until it is set.
    - `cancel_aware`: while blocked on the gate, raise JobCancelled once the cancel event is set.
    - `delay`: seconds to sleep inside `analyze`.
    Every call is recorded in `calls` as (webm_path, ctx).
    """

    def __init__(
        self,
        *,
        ready: bool = True,
        load_gate: threading.Event | None = None,
        load_error: Exception | None = None,
        flags: VideoFlags | None = None,
        analyze_error: Exception | None = None,
        analyze_gate: threading.Event | None = None,
        cancel_aware: bool = False,
        delay: float = 0.0,
    ) -> None:
        self._ready = ready
        self.load_gate = load_gate
        self.load_error = load_error
        self.flags = flags or VideoFlags(False, False)
        self.analyze_error = analyze_error
        self.analyze_gate = analyze_gate
        self.cancel_aware = cancel_aware
        self.delay = delay
        self.load_called = threading.Event()
        self.load_finished = threading.Event()
        self.calls: list[tuple[Path, JobContext]] = []
        self.started = threading.Event()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    @property
    def ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        self.load_called.set()
        try:
            if self.load_gate is not None:
                self.load_gate.wait(10)
            if self.load_error is not None:
                raise self.load_error
            self._ready = True
        finally:
            self.load_finished.set()

    def analyze(self, webm_path: Path, ctx: JobContext) -> VideoFlags:
        with self._lock:
            self.calls.append((webm_path, ctx))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.analyze_gate is not None:
                deadline = time.monotonic() + 10
                while not self.analyze_gate.is_set() and time.monotonic() < deadline:
                    if self.cancel_aware and ctx.cancel_event.is_set():
                        raise JobCancelled()
                    time.sleep(0.01)
            if self.analyze_error is not None:
                raise self.analyze_error
            return self.flags
        finally:
            with self._lock:
                self.active -= 1


@contextmanager
def make_client(settings: Settings, analyzer: Any = None) -> Iterator[TestClient]:
    """A started TestClient (lifespan running) for the given settings and analyzer."""
    app = create_app(settings, analyzer)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    """Poll a sync predicate for up to `timeout` seconds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
