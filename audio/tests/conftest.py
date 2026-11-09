import pytest

from audio.models import SpokenWord
from audio.pipeline import AudioPipeline
from audio.wiring import build_audio_pipeline


def spoken(text: str, step: float = 0.4) -> list[SpokenWord]:
    """Fake ASR output: one SpokenWord per whitespace word, evenly timed."""
    return [
        SpokenWord(text=w, start=i * step, end=i * step + step * 0.8, score=0.9)
        for i, w in enumerate(text.split())
    ]


class FakeTranscriber:
    """Returns a fixed transcript — no model load."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def transcribe(self, wav_path: str) -> list[SpokenWord]:
        return spoken(self._text)


@pytest.fixture
def lane() -> AudioPipeline:
    return build_audio_pipeline(FakeTranscriber())
