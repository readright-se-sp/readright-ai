"""Replay a cached run (devtools/cache_runs.py) through reviewer + post-pass + scorer, no models.

The cache holds what the slow parts produced (CrisperWhisper words, the phoneme tokens); everything
after that is pure Python and runs in milliseconds with any settings override.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio.config import AudioSettings
from audio.lane_b.reviewer import PhoneticReviewer
from audio.models import AudioResult, SpokenWord
from audio.passage import PassagePreparer
from audio.phonetics.g2p import build_g2p
from audio.pipeline import AudioPipeline
from audio.types import PhonemeToken
from audio.wiring import build_audio_pipeline

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "devtools" / "out" / "cache"


def model_ids(settings: AudioSettings) -> dict[str, Any]:
    """What makes a cache entry invalid when it changes."""
    return {
        "asr_model": settings.ASR_MODEL,
        "asr_compute_type": settings.ASR_COMPUTE_TYPE,
        "phoneme_model": settings.PHONEME_MODEL,
        "phoneme_fp16": settings.PHONEME_FP16,
        "phoneme_chunk_s": settings.PHONEME_CHUNK_S,
        "phoneme_context_s": settings.PHONEME_CONTEXT_S,
    }


@dataclass(frozen=True)
class Cached:
    recording: str
    passage_id: str
    passage: str
    spoken: list[SpokenWord]
    lane_a_rows: list[dict[str, Any]]  # WordLane.rows output, before review
    tokens: tuple[PhonemeToken, ...]
    models: dict[str, Any]


def load_cache(path: Path) -> Cached:
    d = json.loads(path.read_text(encoding="utf-8"))
    return Cached(
        recording=d["recording"],
        passage_id=d["passage_id"],
        passage=d["passage"],
        spoken=d["spoken"],
        lane_a_rows=d["lane_a_rows"],
        tokens=tuple(PhonemeToken.model_validate(t) for t in d["phoneme_tokens"]),
        models=d["models"],
    )


def load_all(cache_dir: Path = CACHE_DIR) -> list[Cached]:
    return [load_cache(p) for p in sorted(cache_dir.glob("*.json"))]


class ReplayTranscriber:
    def __init__(self, words: list[SpokenWord]) -> None:
        self._words = words

    def transcribe(self, wav_path: str) -> list[SpokenWord]:
        return self._words


class ReplayRecogniser:
    def __init__(self, tokens: tuple[PhonemeToken, ...]) -> None:
        self._tokens = tokens

    def recognise(self, wav_path: Path, deadline_ms: int | None) -> tuple[PhonemeToken, ...]:
        return self._tokens


class Replayer:
    """Holds the G2P preparers once (espeak is slow to start), builds a pipeline per call."""

    def __init__(self, base: AudioSettings | None = None) -> None:
        self.base = base or AudioSettings()
        g2p = build_g2p(self.base)
        self.passages = PassagePreparer(g2p, self.base.PASSAGE_CACHE_SIZE)
        self.hypotheses = PassagePreparer(g2p, cache_size=0)

    def pipeline(self, c: Cached, settings: AudioSettings | None, phonetic: bool) -> AudioPipeline:
        transcriber = ReplayTranscriber(c.spoken)
        if not phonetic:
            return build_audio_pipeline(transcriber)
        reviewer = PhoneticReviewer(
            ReplayRecogniser(c.tokens),
            self.passages,
            self.hypotheses,
            settings or self.base,
        )
        return build_audio_pipeline(transcriber, reviewer)

    def run(
        self, c: Cached, settings: AudioSettings | None = None, phonetic: bool = True
    ) -> AudioResult:
        return self.pipeline(c, settings, phonetic).run(f"{c.recording}.wav", c.passage)


def with_overrides(base: AudioSettings, **overrides: Any) -> AudioSettings:
    return AudioSettings(_env_file=None, **{**base.model_dump(), **overrides})
