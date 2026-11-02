import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .errors import LaneError
from .models import ErrorCode


class JobCancelled(Exception):
    """The client went away; the analysis should stop."""


@dataclass(frozen=True)
class VideoFlags:
    is_finger_pointing: bool
    is_lack_expression: bool


@dataclass
class JobContext:
    job_id: str
    deadline_ms: int
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def check(self) -> None:
        """Call at least once per frame. Cancel is checked before the deadline."""
        if self.cancel_event.is_set():
            raise JobCancelled()
        if int(time.time() * 1000) >= self.deadline_ms:
            raise LaneError(ErrorCode.DEADLINE_EXCEEDED, "Deadline exceeded.")


class Analyzer(Protocol):
    def load(self) -> None: ...

    @property
    def ready(self) -> bool: ...

    def analyze(self, webm_path: Path, ctx: JobContext) -> VideoFlags: ...


class PlaceholderAnalyzer:
    """Always answers false/false until the real analysis lands (Phases 5-6)."""

    def __init__(self) -> None:
        self._ready = False

    def load(self) -> None:
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    def analyze(self, webm_path: Path, ctx: JobContext) -> VideoFlags:
        ctx.check()
        return VideoFlags(is_finger_pointing=False, is_lack_expression=False)
