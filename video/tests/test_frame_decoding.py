from pathlib import Path

import numpy as np
import pytest
from media_fixtures import (
    make_clip,
    make_header_only_clip,
    make_rotated_clip,
    make_truncated_clip,
    require_ffmpeg,
)
from video_helpers import future_deadline_ms, make_settings

from app.analyzer import JobContext
from app.errors import LaneError
from app.frames import Frame, iter_frames, plan_sampling, probe
from app.models import ErrorCode


def decode(path: Path, **overrides) -> tuple[list[Frame], object]:
    settings = make_settings(**overrides)
    plan = plan_sampling(probe(path, settings), settings)
    ctx = JobContext("job", future_deadline_ms())
    return list(iter_frames(path, plan, ctx, settings)), plan


def test_yields_planned_frames_with_exact_timestamps():
    """Criterion 6."""
    frames, plan = decode(make_clip(seconds=4, rate=30), sample_fps=5.0)
    assert 19 <= len(frames) <= 21
    for i, frame in enumerate(frames):
        assert isinstance(frame, Frame)
        assert frame.index == i
        assert frame.rgb.dtype == np.uint8
        assert frame.rgb.shape == (plan.height, plan.width, 3)
        assert frame.timestamp_ms == round(i * 1000 / plan.fps)
    stamps = [f.timestamp_ms for f in frames]
    assert all(b > a for a, b in zip(stamps, stamps[1:], strict=False))


def test_frames_are_not_blank():
    """Criterion 6."""
    frames, _ = decode(make_clip(seconds=2, rate=30))
    assert frames
    assert int(frames[0].rgb.max()) > int(frames[0].rgb.min())


def test_rotated_video_yields_portrait_frames():
    """Criterion 2."""
    frames, plan = decode(make_rotated_clip(90))
    assert frames
    assert plan.height > plan.width
    for frame in frames:
        assert frame.rgb.shape == (plan.height, plan.width, 3)
        assert frame.rgb.shape[0] > frame.rgb.shape[1]


def test_never_yields_more_than_max_frames():
    """Criterion 7."""
    frames, plan = decode(make_clip(seconds=4, rate=30), sample_fps=10.0, max_frames=7)
    assert plan.max_frames == 7
    assert len(frames) == 7


def test_max_frames_is_enforced_even_if_plan_overshoots():
    """Criterion 7."""
    path = make_clip(seconds=4, rate=30)
    settings = make_settings()
    base = plan_sampling(probe(path, settings), settings)
    plan = type(base)(fps=10.0, width=base.width, height=base.height, max_frames=3)
    frames = list(iter_frames(path, plan, JobContext("job", future_deadline_ms()), settings))
    assert len(frames) == 3


def test_truncated_video_returns_the_frames_it_can_decode():
    """Criterion 11."""
    frames, _ = decode(make_truncated_clip(0.5), sample_fps=5.0)
    assert 1 <= len(frames) < 20


def test_truncated_video_with_no_frames_is_media_unreadable():
    """Criterion 11."""
    require_ffmpeg()
    path = make_header_only_clip()
    settings = make_settings()
    clip_plan = plan_sampling(probe(make_clip(), settings), settings)
    ctx = JobContext("job", future_deadline_ms())
    with pytest.raises(LaneError) as excinfo:
        list(iter_frames(path, clip_plan, ctx, settings))
    assert excinfo.value.code == ErrorCode.MEDIA_UNREADABLE


def test_missing_ffmpeg_binary_is_internal_error():
    """Criterion 12."""
    require_ffmpeg()
    path = make_clip()
    settings = make_settings()
    plan = plan_sampling(probe(path, settings), settings)
    broken = make_settings(ffmpeg_path="definitely-not-a-real-ffmpeg-binary")
    ctx = JobContext("job", future_deadline_ms())
    with pytest.raises(LaneError) as excinfo:
        list(iter_frames(path, plan, ctx, broken))
    assert excinfo.value.code == ErrorCode.INTERNAL
