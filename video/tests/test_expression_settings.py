import pytest
from pydantic import ValidationError

from app.config import Settings


def test_expression_defaults():
    """Criterion 10."""
    settings = Settings(_env_file=None)
    assert settings.expression_min_face_coverage == 0.5
    assert settings.expression_min_face_frames == 20
    assert settings.expression_smooth_frames == 3
    assert settings.expression_min_range == 0.12
    assert settings.expression_group_min_range == {}


def test_group_min_range_is_read_from_json_env(monkeypatch: pytest.MonkeyPatch):
    """Criterion 10."""
    monkeypatch.setenv("MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE", '{"smile": 0.5}')
    settings = Settings(_env_file=None)
    assert settings.expression_group_min_range == {"smile": 0.5}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAI_VIDEO_EXPRESSION_SMOOTH_FRAMES", "4"),
        ("MAI_VIDEO_EXPRESSION_SMOOTH_FRAMES", "0"),
        ("MAI_VIDEO_EXPRESSION_MIN_FACE_COVERAGE", "1.5"),
        ("MAI_VIDEO_EXPRESSION_MIN_FACE_COVERAGE", "-0.1"),
        ("MAI_VIDEO_EXPRESSION_MIN_FACE_FRAMES", "0"),
        ("MAI_VIDEO_EXPRESSION_MIN_RANGE", "1.5"),
        ("MAI_VIDEO_EXPRESSION_MIN_RANGE", "-0.1"),
        ("MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE", '{"not_a_group": 0.2}'),
        ("MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE", '{"smile": 1.5}'),
        ("MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE", '{"smile": -0.1}'),
    ],
)
def test_rejects_invalid_expression_values_at_construction(monkeypatch, name, value):
    """Criterion 10."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_rejects_smoothing_window_wider_than_min_face_frames(monkeypatch):
    """Criterion 10."""
    monkeypatch.setenv("MAI_VIDEO_EXPRESSION_SMOOTH_FRAMES", "21")
    monkeypatch.setenv("MAI_VIDEO_EXPRESSION_MIN_FACE_FRAMES", "20")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_accepts_smoothing_window_equal_to_min_face_frames(monkeypatch):
    """Criterion 10."""
    monkeypatch.setenv("MAI_VIDEO_EXPRESSION_SMOOTH_FRAMES", "21")
    monkeypatch.setenv("MAI_VIDEO_EXPRESSION_MIN_FACE_FRAMES", "21")
    assert Settings(_env_file=None).expression_smooth_frames == 21
