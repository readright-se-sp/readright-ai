"""Feature extraction writes one landmark-only cache file per webm."""

from pathlib import Path

import numpy as np
import pytest
from devtool_helpers import (
    FRAME_COUNT,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    PLAN,
    assert_features_equal,
    default_settings,
    fake_session_factory,
    make_webm,
    patch_video_io,
    sample_features,
)
from devtools.feature_cache import load_cache

from devtools import extract_features

EXPECTED_KEYS = {
    "version",
    "fps",
    "width",
    "height",
    "max_frames",
    "timestamp_ms",
    "blendshape_names",
    "face_present",
    "blendshapes",
    "hand_count",
    "hand_world",
    "hand_image",
    "hand_score",
    "hand_handedness",
    "pose_present",
    "pose",
}


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    path = tmp_path / "cache"
    path.mkdir()
    return path


@pytest.fixture
def extracted(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    patch_video_io(monkeypatch, extract_features)
    settings = default_settings(use_pose=True)
    webm_a = make_webm(tmp_path / "webms", "a.webm")
    webm_b = make_webm(tmp_path / "webms", "b.webm")
    out_a = extract_features.extract_file(webm_a, cache_dir, settings, fake_session_factory())
    out_b = extract_features.extract_file(webm_b, cache_dir, settings, fake_session_factory())
    return out_a, out_b


def test_writes_exactly_one_npz_per_webm_and_nothing_else(
    extracted: tuple[Path, Path], cache_dir: Path
) -> None:
    """Criterion 3."""
    out_a, out_b = extracted
    assert out_a == cache_dir / "a.npz"
    assert out_b == cache_dir / "b.npz"
    assert sorted(p.name for p in cache_dir.iterdir()) == ["a.npz", "b.npz"]


def test_overwrites_an_existing_cache_file_without_adding_files(
    extracted: tuple[Path, Path],
    tmp_path: Path,
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 3."""
    patch_video_io(monkeypatch, extract_features, count=3)
    extract_features.extract_file(
        tmp_path / "webms" / "a.webm", cache_dir, default_settings(), fake_session_factory()
    )
    assert sorted(p.name for p in cache_dir.iterdir()) == ["a.npz", "b.npz"]
    features, _ = load_cache(cache_dir / "a.npz")
    assert len(features) == 3


def test_npz_holds_exactly_the_listed_keys(extracted: tuple[Path, Path]) -> None:
    """Criterion 3."""
    with np.load(extracted[0]) as data:
        assert set(data.files) == EXPECTED_KEYS
        assert data["timestamp_ms"].shape == (FRAME_COUNT,)
        assert data["timestamp_ms"].dtype == np.int64
        assert data["hand_world"].shape[0] == FRAME_COUNT
        assert data["hand_world"].shape[2:] == (21, 3)
        assert data["pose"].shape == (FRAME_COUNT, 33, 4)


def test_stores_no_array_with_a_frame_image_shape(extracted: tuple[Path, Path]) -> None:
    """Criterion 3."""
    pixels = FRAME_HEIGHT * FRAME_WIDTH
    with np.load(extracted[0]) as data:
        for name in data.files:
            array = data[name]
            assert array.dtype != np.uint8, name
            assert array.size < pixels, name
            assert array.shape[-3:] != (FRAME_HEIGHT, FRAME_WIDTH, 3), name


def test_round_trips_through_load_cache_to_equal_features_and_plan(
    extracted: tuple[Path, Path],
) -> None:
    """Criterion 3."""
    features, plan = load_cache(extracted[1])
    assert plan == PLAN
    assert_features_equal(features, [sample_features(i) for i in range(FRAME_COUNT)])
