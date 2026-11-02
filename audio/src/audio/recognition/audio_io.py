"""Reads the lane's input WAV with the stdlib ``wave`` module. Never resamples."""

import wave
from pathlib import Path

import numpy as np

from audio.errors import MediaUnreadable

SAMPLE_RATE = 16_000


def load_wav(path: Path) -> np.ndarray:
    """Return float32 samples in [-1, 1) from a 16 kHz, 16-bit, mono WAV."""
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            n_frames = wav.getnframes()
            if (channels, width, rate) != (1, 2, SAMPLE_RATE):
                raise MediaUnreadable(
                    f"expected 16 kHz / 16-bit / mono, got {rate} Hz / {width * 8}-bit / "
                    f"{channels} ch"
                )
            if n_frames == 0:
                raise MediaUnreadable("the WAV has no frames")
            data = wav.readframes(n_frames)
    except (OSError, EOFError, wave.Error) as exc:
        raise MediaUnreadable(f"cannot read WAV {Path(path).name!r}: {exc}") from exc
    samples = np.frombuffer(data, dtype="<i2")
    if samples.size == 0:
        raise MediaUnreadable("the WAV has no frames")
    return samples.astype(np.float32) / 32768.0
