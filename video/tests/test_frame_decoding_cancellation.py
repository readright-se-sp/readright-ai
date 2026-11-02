import subprocess
import time
from typing import Any

import pytest
from media_fixtures import make_clip
from video_helpers import future_deadline_ms, make_settings, now_ms

from app.analyzer import JobCancelled, JobContext
from app.errors import LaneError
from app.frames import iter_frames, plan_sampling, probe
from app.models import ErrorCode


@pytest.fixture
def handles(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen]:
    """Wrap the real Popen so the test can inspect each ffmpeg process."""
    seen: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen:
        handle = real_popen(*args, **kwargs)
        command = args[0] if args else kwargs["args"]
        if command[-1] == "pipe:1":  # only the decoder, not ffprobe or clip generation
            seen.append(handle)
        return handle

    monkeypatch.setattr("app.frames.subprocess.Popen", recording_popen)
    return seen


def start(ctx: JobContext):
    # A long clip at a high rate so ffmpeg blocks on the pipe while we hold the frame.
    path = make_clip(seconds=10, rate=30)
    settings = make_settings(sample_fps=10.0)
    plan = plan_sampling(probe(path, settings), settings)
    return iter_frames(path, plan, ctx, settings)


def test_cancel_mid_iteration_raises_and_ffmpeg_exits(handles):
    """Criterion 8."""
    ctx = JobContext("job", future_deadline_ms())
    frames = start(ctx)
    next(frames)
    assert len(handles) == 1
    assert handles[0].poll() is None
    ctx.cancel_event.set()
    with pytest.raises(JobCancelled):
        next(frames)
    assert handles[0].poll() is not None


def test_deadline_passing_mid_iteration_raises_and_ffmpeg_exits(handles):
    """Criterion 9."""
    ctx = JobContext("job", future_deadline_ms())
    frames = start(ctx)
    next(frames)
    ctx.deadline_ms = now_ms() + 300
    assert handles[0].poll() is None
    while now_ms() < ctx.deadline_ms:
        time.sleep(0.02)
    with pytest.raises(LaneError) as excinfo:
        next(frames)
    assert excinfo.value.code == ErrorCode.DEADLINE_EXCEEDED
    assert handles[0].poll() is not None


def test_closing_generator_early_kills_ffmpeg(handles):
    """Criterion 10."""
    ctx = JobContext("job", future_deadline_ms())
    frames = start(ctx)
    for _ in frames:
        break
    assert handles[0].poll() is None
    frames.close()
    assert handles[0].poll() is not None


def test_ffmpeg_is_reaped_after_normal_end(handles):
    """Criterion 10."""
    path = make_clip(seconds=1, rate=30)
    settings = make_settings(sample_fps=5.0)
    plan = plan_sampling(probe(path, settings), settings)
    list(iter_frames(path, plan, JobContext("job", future_deadline_ms()), settings))
    assert handles[0].poll() is not None
