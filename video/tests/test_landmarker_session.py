import pytest
from landmark_helpers import REAL_MODEL_DIR, require_models
from media_fixtures import make_clip, require_ffmpeg
from video_helpers import future_deadline_ms, make_settings

from app.analyzer import JobContext
from app.frames import iter_frames, plan_sampling, probe
from app.landmarks import LandmarkerSession, ModelBundle


def decode(settings, seconds=1, rate=10):
    path = make_clip(seconds=seconds, rate=rate)
    plan = plan_sampling(probe(path, settings), settings)
    ctx = JobContext("job", future_deadline_ms())
    return plan, list(iter_frames(path, plan, ctx, settings))


def make_session(**overrides):
    require_models(use_pose=overrides.get("use_pose", False))
    settings = make_settings(model_dir=REAL_MODEL_DIR, **overrides)
    return settings, LandmarkerSession(ModelBundle(settings), settings)


def test_accepts_frames_with_increasing_timestamps_and_finds_nothing_in_testsrc():
    """Criterion 5."""
    require_ffmpeg()
    settings, session = make_session(sample_fps=5.0)
    plan, frames = decode(settings)
    assert len(frames) >= 3
    with session:
        results = [session.process(frame) for frame in frames]
    stamps = [r.timestamp_ms for r in results]
    assert stamps == [f.timestamp_ms for f in frames]
    assert stamps == sorted(set(stamps))
    assert all(r.face is None and r.hands == [] for r in results)


def test_process_after_exit_raises():
    """Criterion 5."""
    require_ffmpeg()
    settings, session = make_session(sample_fps=5.0)
    _, frames = decode(settings)
    with session:
        session.process(frames[0])
    with pytest.raises(RuntimeError):
        session.process(frames[1])


def test_process_before_enter_raises():
    """Criterion 5."""
    require_ffmpeg()
    settings, session = make_session(sample_fps=5.0)
    _, frames = decode(settings)
    with pytest.raises(RuntimeError):
        session.process(frames[0])


def test_exit_is_idempotent():
    """Criterion 5."""
    _, session = make_session()
    with session:
        pass
    session.__exit__(None, None, None)


def test_pose_is_none_on_every_frame_when_pose_is_disabled():
    """Criterion 8."""
    require_ffmpeg()
    settings, session = make_session(sample_fps=5.0, use_pose=False)
    _, frames = decode(settings)
    with session:
        assert all(session.process(frame).pose is None for frame in frames)


def test_pose_landmarker_is_not_created_when_pose_is_disabled(monkeypatch):
    """Criterion 8."""
    from mediapipe.tasks.python import vision

    def forbidden(*args, **kwargs):
        raise AssertionError("pose landmarker must not be created")

    monkeypatch.setattr(vision.PoseLandmarker, "create_from_options", forbidden)
    _, session = make_session(use_pose=False)
    with session:
        pass


def test_pose_runs_when_enabled_and_finds_nothing_in_testsrc():
    """Criterion 8."""
    require_ffmpeg()
    settings, session = make_session(sample_fps=5.0, use_pose=True)
    _, frames = decode(settings)
    with session:
        results = [session.process(frame) for frame in frames]
    assert all(r.pose is None for r in results)
