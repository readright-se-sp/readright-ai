import numpy as np
import pytest
from landmark_helpers import REAL_MODEL_DIR, FakeSession, require_models
from media_fixtures import make_clip, require_ffmpeg
from video_helpers import future_deadline_ms, make_client, make_settings, now_ms

from app.analyzer import JobCancelled, JobContext, MediaPipeAnalyzer, VideoFlags
from app.decision import decide
from app.errors import LaneError
from app.frames import SamplePlan, iter_frames, plan_sampling, probe
from app.landmarks import FaceSample, FrameFeatures, HandSample, LandmarkerSession, ModelBundle
from app.models import ErrorCode

SECRET_VALUE = 0.8765432
SECRET_NAME = "jawOpenSecretName"


class CountingContext(JobContext):
    """A context that counts check() calls and can raise on the Nth one."""

    def __init__(self, error: Exception | None = None, error_at: int | None = None):
        super().__init__("job-1", future_deadline_ms())
        self.checks = 0
        self.error = error
        self.error_at = error_at

    def check(self) -> None:
        self.checks += 1
        if self.error is not None and self.checks == self.error_at:
            raise self.error
        super().check()


@pytest.fixture
def clip():
    return make_clip(seconds=1, rate=10)


@pytest.fixture
def settings():
    return make_settings(sample_fps=5.0)


def decoded_count(clip, settings) -> int:
    plan = plan_sampling(probe(clip, settings), settings)
    ctx = JobContext("job", future_deadline_ms())
    return len(list(iter_frames(clip, plan, ctx, settings)))


def hand_sample() -> HandSample:
    return HandSample(np.zeros((21, 3)), np.zeros((21, 3)), "Left", 0.9)


def test_processes_each_decoded_frame_once_and_checks_context_per_frame(clip, settings):
    """Criterion 7."""
    expected = decoded_count(clip, settings)
    fake = FakeSession()
    ctx = CountingContext()
    flags = MediaPipeAnalyzer(settings, session_factory=fake.factory()).analyze(clip, ctx)
    assert flags == VideoFlags(False, False)
    assert len(fake.frames) == expected >= 3
    assert [f.index for f in fake.frames] == list(range(expected))
    assert ctx.checks >= expected


def test_closes_session_after_success(clip, settings):
    """Criterion 7."""
    fake = FakeSession()
    MediaPipeAnalyzer(settings, session_factory=fake.factory()).analyze(clip, CountingContext())
    assert fake.entered and fake.closed


def test_closes_session_when_job_is_cancelled(clip, settings):
    """Criterion 7."""
    fake = FakeSession()
    ctx = CountingContext(error=JobCancelled(), error_at=3)
    analyzer = MediaPipeAnalyzer(settings, session_factory=fake.factory())
    with pytest.raises(JobCancelled):
        analyzer.analyze(clip, ctx)
    assert fake.closed


def test_closes_session_when_deadline_is_exceeded(clip, settings):
    """Criterion 7."""
    fake = FakeSession()
    error = LaneError(ErrorCode.DEADLINE_EXCEEDED, "Deadline exceeded.")
    ctx = CountingContext(error=error, error_at=3)
    analyzer = MediaPipeAnalyzer(settings, session_factory=fake.factory())
    with pytest.raises(LaneError) as info:
        analyzer.analyze(clip, ctx)
    assert info.value.code == ErrorCode.DEADLINE_EXCEEDED
    assert fake.entered and fake.closed
    assert len(fake.frames) >= 1


def test_closes_session_when_deadline_is_already_past(clip, settings):
    """Criterion 7."""
    fake = FakeSession()
    ctx = JobContext("job-1", now_ms() - 1000)
    analyzer = MediaPipeAnalyzer(settings, session_factory=fake.factory())
    with pytest.raises(LaneError) as info:
        analyzer.analyze(clip, ctx)
    assert info.value.code == ErrorCode.DEADLINE_EXCEEDED
    assert fake.entered and fake.closed


def test_closes_session_when_processing_fails(clip, settings):
    """Criterion 7."""
    fake = FakeSession(error=ValueError("boom"), error_at=2)
    analyzer = MediaPipeAnalyzer(settings, session_factory=fake.factory())
    with pytest.raises(ValueError, match="boom"):
        analyzer.analyze(clip, CountingContext())
    assert fake.closed
    assert len(fake.frames) == 2


def test_decide_returns_false_flags_and_frame_counts():
    """Criterion 9."""
    settings = make_settings()
    plan = SamplePlan(fps=5.0, width=320, height=240, max_frames=900)
    face = FaceSample({"jawOpen": 0.5})
    features = [
        FrameFeatures(0, face, [hand_sample()], None),
        FrameFeatures(200, face, [], None),
        FrameFeatures(400, None, [hand_sample(), hand_sample()], None),
        FrameFeatures(600, None, [], None),
    ]
    decision = decide(features, plan, settings)
    assert decision.flags == VideoFlags(False, False)
    assert decision.stats["frames"] == 4
    assert decision.stats["face_frames"] == 2
    assert decision.stats["hand_frames"] == 2


def test_decide_handles_no_frames():
    """Criterion 9."""
    settings = make_settings()
    decision = decide([], SamplePlan(5.0, 320, 240, 900), settings)
    assert decision.flags == VideoFlags(False, False)
    assert decision.stats["frames"] == 0
    assert decision.stats["face_frames"] == 0
    assert decision.stats["hand_frames"] == 0


def test_logs_stats_without_landmark_values(clip, settings, log_records):
    """Criterion 9."""

    def features(frame):
        return FrameFeatures(
            frame.timestamp_ms, FaceSample({SECRET_NAME: SECRET_VALUE}), [hand_sample()], None
        )

    fake = FakeSession(features=features)
    analyzer = MediaPipeAnalyzer(settings, session_factory=fake.factory())
    analyzer.analyze(clip, CountingContext())
    finished = [r for r in log_records.records if r.getMessage() == "analysis finished"]
    assert len(finished) == 1
    record = finished[0]
    count = len(fake.frames)
    assert record.frames == count
    assert record.face_frames == count
    assert record.hand_frames == count
    everything = " ".join(
        [r.getMessage() + repr(sorted(r.__dict__.items())) for r in log_records.records]
    )
    assert str(SECRET_VALUE) not in everything
    assert SECRET_NAME not in everything
    assert str(clip) not in everything
    for r in log_records.records:
        assert not any(isinstance(v, np.ndarray) for v in r.__dict__.values())


def test_analyzes_testsrc_clip_with_real_models(clip):
    """Criterion 4."""
    require_ffmpeg()
    require_models()
    real = make_settings(model_dir=REAL_MODEL_DIR, sample_fps=5.0)
    analyzer = MediaPipeAnalyzer(real)
    analyzer.load()
    assert analyzer.ready
    flags = analyzer.analyze(clip, JobContext("job-1", future_deadline_ms()))
    assert flags == VideoFlags(is_finger_pointing=False, is_lack_expression=False)


def test_real_features_are_empty_for_testsrc_with_planned_timestamps(clip):
    """Criterion 4."""
    require_ffmpeg()
    require_models()
    real = make_settings(model_dir=REAL_MODEL_DIR, sample_fps=5.0)
    plan = plan_sampling(probe(clip, real), real)
    ctx = JobContext("job-1", future_deadline_ms())
    with LandmarkerSession(ModelBundle(real), real) as session:
        features = [session.process(f) for f in iter_frames(clip, plan, ctx, real)]
    assert len(features) >= 3
    assert all(f.face is None and f.hands == [] for f in features)
    assert [f.timestamp_ms for f in features] == [
        round(i * 1000 / plan.fps) for i in range(len(features))
    ]


def test_default_app_analyzer_is_mediapipe_and_not_ready_without_models(tmp_path):
    """Criterion 10."""
    settings = make_settings(model_dir=tmp_path / "no-models")
    with make_client(settings) as client:
        assert isinstance(client.app.state.analyzer, MediaPipeAnalyzer)
        assert client.get("/health/ready").status_code == 503
        assert not client.app.state.analyzer.ready


def test_constructing_analyzer_does_not_touch_model_files(tmp_path):
    """Criterion 10."""
    analyzer = MediaPipeAnalyzer(make_settings(model_dir=tmp_path / "no-models"))
    assert not analyzer.ready
