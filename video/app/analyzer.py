import contextlib
import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .config import Settings
from .errors import LaneError
from .models import ErrorCode

if TYPE_CHECKING:
    from .frames import Frame
    from .landmarks import FrameFeatures, ModelBundle

logger = logging.getLogger(__name__)


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


class SessionLike(Protocol):
    def process(self, frame: "Frame") -> "FrameFeatures": ...


class MediaPipeAnalyzer:
    """Runs the MediaPipe landmarkers over sampled frames and decides the flags."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], AbstractContextManager[SessionLike]] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._bundle: ModelBundle | None = None
        self._ready = False

    def load(self) -> None:
        # Imported here because frames and landmarks import this module.
        from .landmarks import ModelBundle

        bundle = ModelBundle(self._settings)
        bundle.validate()
        self._bundle = bundle
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    def _new_session(self) -> AbstractContextManager[SessionLike]:
        if self._session_factory is not None:
            return self._session_factory()
        from .landmarks import LandmarkerSession

        assert self._bundle is not None
        return LandmarkerSession(self._bundle, self._settings)

    def analyze(self, webm_path: Path, ctx: JobContext) -> VideoFlags:
        from .decision import decide
        from .frames import iter_frames, plan_sampling, probe

        settings = self._settings
        plan = plan_sampling(probe(webm_path, settings), settings)
        features: list[FrameFeatures] = []
        with (
            self._new_session() as session,
            contextlib.closing(iter_frames(webm_path, plan, ctx, settings)) as frames,
        ):
            for frame in frames:
                ctx.check()
                features.append(session.process(frame))
        decision = decide(features, plan, settings)
        logger.info("analysis finished", extra=decision.stats)
        return decision.flags
