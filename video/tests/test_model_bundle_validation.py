import threading
from pathlib import Path

import pytest
from landmark_helpers import FACE, HAND, POSE, REAL_MODEL_DIR, require_models, write_lock
from video_helpers import make_client, make_settings, wait_until

from app.analyzer import MediaPipeAnalyzer
from app.landmarks import ModelBundle, ModelError

PAYLOADS = {FACE: b"face", HAND: b"hand", POSE: b"pose"}


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return write_lock(tmp_path / "models.lock", PAYLOADS)


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    path = tmp_path / "models"
    path.mkdir()
    return path


def populate(model_dir: Path, *names: str) -> None:
    for name in names:
        (model_dir / name).write_bytes(PAYLOADS[name])


def test_bundle_reports_missing_face_model_by_name(model_dir, lock):
    """Criterion 2."""
    populate(model_dir, HAND)
    with pytest.raises(ModelError) as info:
        ModelBundle(make_settings(model_dir=model_dir), lock)
    assert FACE in str(info.value)
    assert str(model_dir) not in str(info.value)


def test_bundle_reports_missing_hand_model_by_name(model_dir, lock):
    """Criterion 2."""
    populate(model_dir, FACE)
    with pytest.raises(ModelError, match=HAND):
        ModelBundle(make_settings(model_dir=model_dir), lock)


def test_rejects_model_with_wrong_hash(model_dir, lock):
    """Criterion 2."""
    populate(model_dir, HAND)
    (model_dir / FACE).write_bytes(b"tampered")
    with pytest.raises(ModelError) as info:
        ModelBundle(make_settings(model_dir=model_dir), lock)
    assert FACE in str(info.value)


def test_bundle_without_pose_does_not_require_pose_file(model_dir, lock):
    """Criterion 8."""
    populate(model_dir, FACE, HAND)
    bundle = ModelBundle(make_settings(model_dir=model_dir, use_pose=False), lock)
    assert bundle.face_path == model_dir / FACE
    assert bundle.hand_path == model_dir / HAND
    assert bundle.pose_path is None


def test_bundle_with_pose_requires_pose_file(model_dir, lock):
    """Criterion 8."""
    populate(model_dir, FACE, HAND)
    with pytest.raises(ModelError, match=POSE):
        ModelBundle(make_settings(model_dir=model_dir, use_pose=True), lock)


def test_bundle_with_pose_exposes_pose_path(model_dir, lock):
    """Criterion 8."""
    populate(model_dir, FACE, HAND, POSE)
    bundle = ModelBundle(make_settings(model_dir=model_dir, use_pose=True), lock)
    assert bundle.pose_path == model_dir / POSE


def test_load_fails_and_analyzer_stays_not_ready_when_models_are_missing(tmp_path):
    """Criterion 2."""
    analyzer = MediaPipeAnalyzer(make_settings(model_dir=tmp_path / "empty"))
    with pytest.raises(ModelError):
        analyzer.load()
    assert not analyzer.ready


def test_health_ready_stays_503_when_models_are_missing(tmp_path):
    """Criterion 2."""
    settings = make_settings(model_dir=tmp_path / "empty")
    attempted = threading.Event()

    class SignallingAnalyzer(MediaPipeAnalyzer):
        def load(self) -> None:
            try:
                super().load()
            finally:
                attempted.set()

    analyzer = SignallingAnalyzer(settings)
    with make_client(settings, analyzer) as client:
        assert wait_until(attempted.is_set, timeout=5)
        assert client.get("/health/ready").status_code == 503
        assert not analyzer.ready


def test_load_succeeds_with_real_models_and_lane_becomes_ready():
    """Criterion 3."""
    model_dir = require_models()
    settings = make_settings(model_dir=model_dir)
    analyzer = MediaPipeAnalyzer(settings)
    assert not analyzer.ready
    with make_client(settings, analyzer) as client:
        assert wait_until(lambda: client.get("/health/ready").status_code == 200, timeout=30)
    assert analyzer.ready


def test_real_bundle_validates_without_pose():
    """Criterion 3."""
    model_dir = require_models()
    bundle = ModelBundle(make_settings(model_dir=model_dir))
    bundle.validate()
    assert bundle.pose_path is None


def test_real_bundle_validates_with_pose():
    """Criterion 8."""
    require_models(use_pose=True)
    bundle = ModelBundle(make_settings(model_dir=REAL_MODEL_DIR, use_pose=True))
    bundle.validate()
    assert bundle.pose_path == REAL_MODEL_DIR / POSE
