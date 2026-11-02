import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

VIDEO_ROOT = Path(__file__).resolve().parent.parent


def test_media_settings_have_documented_defaults():
    """Criterion 14."""
    settings = Settings(_env_file=None)
    assert settings.ffmpeg_path == "ffmpeg"
    assert settings.ffprobe_path == "ffprobe"
    assert settings.probe_timeout_seconds == 10
    assert settings.sample_fps == 5.0
    assert settings.frame_width == 640
    assert settings.max_frames == 900


def test_media_settings_read_prefixed_env(monkeypatch: pytest.MonkeyPatch):
    """Criterion 14."""
    monkeypatch.setenv("MAI_VIDEO_FFMPEG_PATH", "/opt/ff/ffmpeg")
    monkeypatch.setenv("MAI_VIDEO_FFPROBE_PATH", "/opt/ff/ffprobe")
    monkeypatch.setenv("MAI_VIDEO_PROBE_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("MAI_VIDEO_SAMPLE_FPS", "2")
    monkeypatch.setenv("MAI_VIDEO_FRAME_WIDTH", "128")
    monkeypatch.setenv("MAI_VIDEO_MAX_FRAMES", "10")
    settings = Settings(_env_file=None)
    assert settings.ffmpeg_path == "/opt/ff/ffmpeg"
    assert settings.ffprobe_path == "/opt/ff/ffprobe"
    assert settings.probe_timeout_seconds == 2.5
    assert settings.sample_fps == 2.0
    assert settings.frame_width == 128
    assert settings.max_frames == 10


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAI_VIDEO_PROBE_TIMEOUT_SECONDS", "0"),
        ("MAI_VIDEO_PROBE_TIMEOUT_SECONDS", "-1"),
        ("MAI_VIDEO_SAMPLE_FPS", "0"),
        ("MAI_VIDEO_SAMPLE_FPS", "-5"),
        ("MAI_VIDEO_FRAME_WIDTH", "63"),
        ("MAI_VIDEO_FRAME_WIDTH", "0"),
        ("MAI_VIDEO_MAX_FRAMES", "0"),
        ("MAI_VIDEO_MAX_FRAMES", "-3"),
    ],
)
def test_rejects_invalid_media_values_at_construction(monkeypatch, name, value):
    """Criterion 14."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_accepts_boundary_media_values(monkeypatch: pytest.MonkeyPatch):
    """Criterion 14."""
    monkeypatch.setenv("MAI_VIDEO_FRAME_WIDTH", "64")
    monkeypatch.setenv("MAI_VIDEO_MAX_FRAMES", "1")
    settings = Settings(_env_file=None)
    assert settings.frame_width == 64
    assert settings.max_frames == 1


@pytest.mark.parametrize(
    "command",
    [
        "winget install Gyan.FFmpeg",
        "brew install ffmpeg",
        "sudo apt install ffmpeg",
    ],
)
def test_readme_lists_ffmpeg_install_commands(command: str):
    """Criterion 14."""
    text = (VIDEO_ROOT / "README.md").read_text(encoding="utf-8")
    assert command in text


def test_readme_prerequisites_no_longer_defer_ffmpeg():
    """Criterion 14."""
    text = (VIDEO_ROOT / "README.md").read_text(encoding="utf-8")
    assert not re.search(r"needed from Phase 2", text)
