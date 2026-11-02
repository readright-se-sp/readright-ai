import threading
import time

import pytest

from app.analyzer import JobCancelled, JobContext
from app.errors import LaneError
from app.models import ErrorCode


def future_ms() -> int:
    return int(time.time() * 1000) + 60_000


def test_check_returns_when_not_cancelled_and_before_deadline():
    """Criterion 14."""
    ctx = JobContext(job_id="j", deadline_ms=future_ms())
    assert ctx.check() is None


def test_check_raises_cancelled_when_event_is_set():
    """Criterion 14."""
    ctx = JobContext(job_id="j", deadline_ms=future_ms())
    ctx.cancel_event.set()
    with pytest.raises(JobCancelled):
        ctx.check()


def test_check_raises_deadline_exceeded_after_deadline():
    """Criterion 14."""
    ctx = JobContext(job_id="j", deadline_ms=int(time.time() * 1000) - 1)
    with pytest.raises(LaneError) as excinfo:
        ctx.check()
    assert excinfo.value.code == ErrorCode.DEADLINE_EXCEEDED


def test_check_reports_cancel_before_deadline():
    """Criterion 14."""
    ctx = JobContext(job_id="j", deadline_ms=0)
    ctx.cancel_event.set()
    with pytest.raises(JobCancelled):
        ctx.check()


def test_each_context_gets_its_own_cancel_event():
    """Criterion 14."""
    first = JobContext(job_id="a", deadline_ms=future_ms())
    second = JobContext(job_id="b", deadline_ms=future_ms())
    assert isinstance(first.cancel_event, threading.Event)
    assert first.cancel_event is not second.cancel_event
