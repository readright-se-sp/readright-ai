"""Builders for real WAV files and miscue responses shared by the clip and upload tests."""

import os
import wave
from pathlib import Path
from uuid import UUID

import pytest

from app.models import AnalysisResponse, Miscue, MiscueType

RATE = 16000
FRAMES_PER_MS = RATE // 1000
REQUEST_ID = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"
BUCKET = "test-bucket"
REGION = "ap-southeast-1"


def sample_bytes(total_frames: int, offset: int = 0) -> bytes:
    """16-bit mono PCM where frame i holds the distinctive value ((i + offset) % 30000) + 1."""
    out = bytearray()
    for i in range(total_frames):
        out += (((i + offset) % 30000) + 1).to_bytes(2, "little", signed=True)
    return bytes(out)


def write_wav(path: Path, duration_ms: int, offset: int = 0) -> bytes:
    """Write a 16 kHz mono 16-bit WAV of duration_ms; returns its raw sample bytes."""
    pcm = sample_bytes(duration_ms * FRAMES_PER_MS, offset)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm)
    return pcm


def expected_range(
    duration_ms: int, start_ms: int, end_ms: int, padding_ms: int
) -> tuple[int, int]:
    """Frame range [first, last) of a clip, straight from the spec formula."""
    s = max(0, start_ms - padding_ms)
    e = min(duration_ms, end_ms + padding_ms)
    return (s * RATE) // 1000, (e * RATE) // 1000


def read_wav_bytes(data: bytes, tmp_path: Path):
    """Parse WAV bytes with the wave module (via a temp file); returns params and raw frames."""
    path = tmp_path / "parsed.wav"
    path.write_bytes(data)
    with wave.open(str(path), "rb") as src:
        return src.getparams(), src.readframes(src.getnframes())


def make_response(spans: list[tuple[int, int]], request_id: str = REQUEST_ID) -> AnalysisResponse:
    """A response with one miscue per (read_start, read_end) span and deterministic keys."""
    prefix = f"analyses/{request_id}/miscues/"
    miscues = [
        Miscue(
            actual_phrase="cat",
            spoken_phrase="cap",
            actual_phoneme=["k", "ae", "t"],
            spoken_phoneme=["k", "ae", "p"],
            read_start=start,
            read_end=end,
            audio_object_key=f"{prefix}miscue-{n:04d}.wav",
            miscue_type=MiscueType.SUBSTITUTION,
        )
        for n, (start, end) in enumerate(spans, start=1)
    ]
    return AnalysisResponse(
        request_id=UUID(request_id),
        reading_score=85.0,
        wpm=60.0,
        is_word_by_word=False,
        is_disregard_punctuation=True,
        is_monotonous=False,
        is_lack_expression=False,
        is_hardly_audible=False,
        is_finger_pointing=False,
        miscues=miscues,
    )


def use_fake_aws(
    monkeypatch: pytest.MonkeyPatch, key_id: str = "testing", secret: str = "testing"
) -> None:
    """Fake credentials and region; drop any real AWS_* variable so nothing leaks to real AWS."""
    for name in list(os.environ):
        if name.upper().startswith("AWS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", key_id)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
