import pytest
from video_helpers import make_settings

from app.frames import SamplePlan, VideoInfo, plan_sampling


def info(width=1280, height=720, duration_ms: int | None = 10_000) -> VideoInfo:
    return VideoInfo(width=width, height=height, rotation=0, duration_ms=duration_ms)


def test_keeps_sample_fps_for_a_ten_second_video():
    """Criterion 5."""
    settings = make_settings(sample_fps=5.0, max_frames=900)
    plan = plan_sampling(info(duration_ms=10_000), settings)
    assert isinstance(plan, SamplePlan)
    assert plan.fps == pytest.approx(5.0)
    assert plan.max_frames == 900


def test_lowers_fps_so_long_video_fits_max_frames():
    """Criterion 5."""
    settings = make_settings(sample_fps=5.0, max_frames=100)
    duration_ms = 60_000
    plan = plan_sampling(info(duration_ms=duration_ms), settings)
    assert plan.fps < 5.0
    assert plan.fps == pytest.approx(100 * 1000 / duration_ms)
    assert plan.fps * duration_ms / 1000 <= 100 + 1e-9
    assert plan.max_frames == 100


def test_uses_sample_fps_when_duration_unknown():
    """Criterion 5."""
    settings = make_settings(sample_fps=3.0)
    plan = plan_sampling(info(duration_ms=None), settings)
    assert plan.fps == pytest.approx(3.0)


def test_never_upscales_beyond_source_width():
    """Criterion 5."""
    settings = make_settings(frame_width=640)
    plan = plan_sampling(info(width=320, height=240), settings)
    assert plan.width == 320
    assert plan.height == 240


def test_downscales_wide_source_to_frame_width():
    """Criterion 5."""
    settings = make_settings(frame_width=640)
    plan = plan_sampling(info(width=1280, height=720), settings)
    assert plan.width == 640
    assert plan.height == 360


@pytest.mark.parametrize(
    ("width", "height"),
    [(1281, 721), (333, 777), (641, 481), (99, 101), (1920, 1080), (720, 1280), (65, 4000)],
)
def test_dimensions_are_even_and_keep_aspect_ratio(width, height):
    """Criterion 5."""
    settings = make_settings(frame_width=640)
    plan = plan_sampling(info(width=width, height=height), settings)
    assert plan.width % 2 == 0
    assert plan.height % 2 == 0
    assert plan.width <= width
    assert plan.height >= 2
    assert abs(plan.height - plan.width * height / width) <= 1.0


def test_odd_frame_width_setting_is_rounded_down_to_even():
    """Criterion 5."""
    settings = make_settings(frame_width=65)
    plan = plan_sampling(info(width=1280, height=720), settings)
    assert plan.width == 64
