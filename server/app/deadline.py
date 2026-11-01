import time
from collections.abc import Callable
from contextvars import ContextVar


class Deadline:
    """A point in time by which the current request must finish."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock())

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0


deadline_var: ContextVar[Deadline | None] = ContextVar("deadline", default=None)
