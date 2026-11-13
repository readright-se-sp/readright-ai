import wave

import numpy as np
import pytest

from audio.errors import MediaUnreadable
from audio.recognition.audio_io import load_wav


def write_wav(path, samples, *, rate=16000, width=2, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples)


def test_valid_wav_loads_with_right_length_and_range(tmp_path):
    pcm = np.array([0, 16384, -16384, 32767, -32768], dtype="<i2")
    write_wav(tmp_path / "a.wav", pcm.tobytes())
    out = load_wav(tmp_path / "a.wav")
    assert out.dtype == np.float32
    assert len(out) == 5
    assert out.min() == -1.0
    assert out.max() < 1.0
    assert out[1] == 0.5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"channels": 2},
        {"rate": 8000},
        {"width": 3},
        {"width": 1},
    ],
)
def test_wrong_format_raises(tmp_path, kwargs):
    width = kwargs.get("width", 2)
    channels = kwargs.get("channels", 1)
    write_wav(tmp_path / "a.wav", bytes(width * channels * 100), **kwargs)
    with pytest.raises(MediaUnreadable):
        load_wav(tmp_path / "a.wav")


def test_empty_wav_raises(tmp_path):
    write_wav(tmp_path / "a.wav", b"")
    with pytest.raises(MediaUnreadable):
        load_wav(tmp_path / "a.wav")


def test_missing_file_raises(tmp_path):
    with pytest.raises(MediaUnreadable):
        load_wav(tmp_path / "nope.wav")


def test_not_a_wav_raises(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"this is not a wav file at all")
    with pytest.raises(MediaUnreadable):
        load_wav(tmp_path / "a.wav")
