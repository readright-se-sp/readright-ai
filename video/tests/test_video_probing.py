from pathlib import Path

import pytest
from media_fixtures import (
    make_audio_only_clip,
    make_clip,
    make_durationless_clip,
    make_rotated_clip,
    require_ffmpeg,
)
from video_helpers import make_settings

from app.errors import LaneError
from app.frames import VideoInfo, probe
from app.models import ErrorCode


@pytest.mark.parametrize("codec", ["vp8", "vp9"])
def test_reports_size_and_duration_for_vp8_and_vp9(codec: str):
    """Criterion 1."""
    info = probe(make_clip(codec, seconds=4, rate=30), make_settings())
    assert isinstance(info, VideoInfo)
    assert (info.width, info.height) == (320, 240)
    assert info.rotation == 0
    assert info.duration_ms is not None
    assert abs(info.duration_ms - 4000) <= 100


def test_swaps_display_size_for_ninety_degree_rotation():
    """Criterion 2."""
    info = probe(make_rotated_clip(90), make_settings())
    assert info.rotation == 90
    assert (info.width, info.height) == (240, 320)


def test_derives_duration_from_packets_when_header_has_none():
    """Criterion 3."""
    info = probe(make_durationless_clip(seconds=4, rate=30), make_settings())
    assert info.duration_ms is not None
    # Within one frame (33 ms) of the real 4 s length; the last pts is one frame short.
    assert abs(info.duration_ms - 4000) <= 34


def test_rejects_audio_only_webm():
    """Criterion 4."""
    with pytest.raises(LaneError) as excinfo:
        probe(make_audio_only_clip(), make_settings())
    assert excinfo.value.code == ErrorCode.MEDIA_UNREADABLE


def test_rejects_text_file_renamed_webm(tmp_path):
    """Criterion 4."""
    require_ffmpeg()
    path = tmp_path / "notes.webm"
    path.write_text("this is not a video\n" * 20)
    with pytest.raises(LaneError) as excinfo:
        probe(path, make_settings())
    assert excinfo.value.code == ErrorCode.MEDIA_UNREADABLE


def test_rejects_empty_file(tmp_path):
    """Criterion 4."""
    require_ffmpeg()
    path = tmp_path / "empty.webm"
    path.write_bytes(b"")
    with pytest.raises(LaneError) as excinfo:
        probe(path, make_settings())
    assert excinfo.value.code == ErrorCode.MEDIA_UNREADABLE


def test_missing_ffprobe_binary_is_internal_error():
    """Criterion 12."""
    settings = make_settings(ffprobe_path="definitely-not-a-real-ffprobe-binary")
    with pytest.raises(LaneError) as excinfo:
        probe(Path("clip.webm"), settings)
    assert excinfo.value.code == ErrorCode.INTERNAL
