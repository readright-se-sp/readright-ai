import pytest
from pydantic import ValidationError
from video_helpers import make_settings

from app.config import Settings

DEFAULTS = {
    "pointing_min_hand_score": 0.5,
    "pointing_extended_max_bend_deg": 35,
    "pointing_curled_min_bend_deg": 60,
    "pointing_merge_gap_ms": 400,
    "pointing_min_episode_ms": 600,
    "pointing_min_total_ms": 2000,
    "pointing_min_total_fraction": 0.05,
    "pointing_use_sweeps": True,
    "sweep_min_ms": 1000,
    "sweep_min_dx": 0.10,
    "sweep_return_max_ms": 1000,
    "sweep_min_count": 2,
    "pointing_pose_min_visibility": 0.5,
    "pointing_pose_bottom_band": 0.3,
}


def test_pointing_defaults():
    """Criterion 12."""
    settings = Settings(_env_file=None)
    for name, value in DEFAULTS.items():
        assert getattr(settings, name) == value, name


def test_pointing_settings_are_read_from_env(monkeypatch: pytest.MonkeyPatch):
    """Criterion 12."""
    monkeypatch.setenv("MAI_VIDEO_POINTING_USE_SWEEPS", "false")
    monkeypatch.setenv("MAI_VIDEO_SWEEP_MIN_COUNT", "4")
    settings = Settings(_env_file=None)
    assert settings.pointing_use_sweeps is False
    assert settings.sweep_min_count == 4


INVALID = [
    ("pointing_extended_max_bend_deg", -1),
    ("pointing_extended_max_bend_deg", 181),
    ("pointing_curled_min_bend_deg", -1),
    ("pointing_curled_min_bend_deg", 181),
    ("pointing_min_hand_score", -0.1),
    ("pointing_min_hand_score", 1.1),
    ("pointing_min_total_fraction", -0.1),
    ("pointing_min_total_fraction", 1.1),
    ("pointing_pose_min_visibility", -0.1),
    ("pointing_pose_min_visibility", 1.1),
    ("pointing_pose_bottom_band", -0.1),
    ("pointing_pose_bottom_band", 1.1),
    ("pointing_merge_gap_ms", 0),
    ("pointing_merge_gap_ms", -5),
    ("pointing_min_episode_ms", 0),
    ("pointing_min_episode_ms", -5),
    ("pointing_min_total_ms", 0),
    ("pointing_min_total_ms", -5),
    ("sweep_min_ms", 0),
    ("sweep_min_ms", -5),
    ("sweep_return_max_ms", 0),
    ("sweep_return_max_ms", -5),
    ("sweep_min_dx", 0),
    ("sweep_min_dx", -0.1),
    ("sweep_min_dx", 1.1),
    ("sweep_min_count", 0),
    ("sweep_min_count", -1),
]


@pytest.mark.parametrize(("name", "value"), INVALID)
def test_rejects_invalid_pointing_values_at_construction(name, value):
    """Criterion 12."""
    with pytest.raises(ValidationError):
        make_settings(**{name: value})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("pointing_extended_max_bend_deg", 0),
        ("pointing_extended_max_bend_deg", 180),
        ("pointing_curled_min_bend_deg", 0),
        ("pointing_curled_min_bend_deg", 180),
        ("pointing_min_hand_score", 0),
        ("pointing_min_hand_score", 1),
        ("pointing_min_total_fraction", 0),
        ("pointing_min_total_fraction", 1),
        ("pointing_pose_min_visibility", 0),
        ("pointing_pose_min_visibility", 1),
        ("pointing_pose_bottom_band", 0),
        ("pointing_pose_bottom_band", 1),
        ("sweep_min_dx", 1),
        ("sweep_min_count", 1),
    ],
)
def test_accepts_boundary_pointing_values(name, value):
    """Criterion 12."""
    assert getattr(make_settings(**{name: value}), name) == value
