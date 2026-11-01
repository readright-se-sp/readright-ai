"""Cutting a padded span out of a WAV file."""

import pytest
from audio_helpers import FRAMES_PER_MS, RATE, expected_range, read_wav_bytes, write_wav

from app.clips import cut_clip, wav_duration_ms
from app.errors import MAIError
from app.models import ErrorCode

DURATION_MS = 3000


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "audio.wav"
    pcm = write_wav(path, DURATION_MS)
    return path, pcm


def test_duration_in_milliseconds(source):
    # criterion 3
    path, _ = source
    assert wav_duration_ms(path) == DURATION_MS


def test_duration_of_a_zero_length_file(tmp_path):
    # criterion 3
    path = tmp_path / "empty.wav"
    write_wav(path, 0)
    assert wav_duration_ms(path) == 0


@pytest.mark.parametrize(
    ("start_ms", "end_ms", "padding_ms"),
    [
        (1000, 1400, 150),  # plain
        (1000, 1400, 0),  # no padding
        (50, 300, 150),  # padding clamped at the start
        (2800, 2950, 150),  # padding clamped at the end
        (2800, 5000, 150),  # end_ms past the recording is clamped
        (0, 3000, 0),  # whole recording
        (1500, 1500, 150),  # omission: zero-length span plus padding
        (1001, 1403, 7),  # odd values; frame boundaries computed with floor
    ],
)
def test_clip_holds_exactly_the_padded_frame_range(source, tmp_path, start_ms, end_ms, padding_ms):
    # criterion 4
    path, pcm = source
    first, last = expected_range(DURATION_MS, start_ms, end_ms, padding_ms)

    params, frames = read_wav_bytes(cut_clip(path, start_ms, end_ms, padding_ms), tmp_path)

    assert (params.framerate, params.nchannels, params.sampwidth) == (RATE, 1, 2)
    assert params.nframes == last - first
    assert frames == pcm[first * 2 : last * 2]


def test_clip_frame_count_follows_floor_formula(source, tmp_path):
    # criterion 4: s=850, e=1550 -> 700 ms -> 11200 frames
    path, _ = source
    params, _ = read_wav_bytes(cut_clip(path, 1000, 1400, 150), tmp_path)
    assert params.nframes == 700 * FRAMES_PER_MS


def test_clip_is_a_complete_wav_file(source):
    # criterion 4
    path, _ = source
    data = cut_clip(path, 1000, 1400, 150)
    assert isinstance(data, bytes)
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"


def test_rejects_miscue_starting_at_recording_end(source):
    # criterion 5
    path, _ = source
    with pytest.raises(MAIError) as raised:
        cut_clip(path, DURATION_MS, DURATION_MS + 100, 150)
    assert raised.value.code == ErrorCode.PROCESSING_FAILED
    assert raised.value.details == {"lane": "audio"}


def test_rejects_miscue_starting_after_recording_end(source):
    # criterion 5
    path, _ = source
    with pytest.raises(MAIError) as raised:
        cut_clip(path, DURATION_MS + 500, DURATION_MS + 900, 0)
    assert raised.value.code == ErrorCode.PROCESSING_FAILED
    assert raised.value.details == {"lane": "audio"}


def test_zero_frame_clip_is_a_valid_empty_wav(source, tmp_path):
    # criterion 5: padding 0 and start == end, in bounds
    path, _ = source
    params, frames = read_wav_bytes(cut_clip(path, 1200, 1200, 0), tmp_path)
    assert params.nframes == 0
    assert frames == b""
    assert (params.framerate, params.nchannels, params.sampwidth) == (RATE, 1, 2)
