from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioSettings(BaseSettings):
    """Audio service config. Env vars are prefixed AUDIO_ (e.g. AUDIO_ASR_MODEL)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AUDIO_",
        extra="ignore",
    )

    ASR_MODEL: str = (
        "models/crisper2_large_ct2"  # CTranslate2 conversion of nyralabs/CrisperWhisper2.0_large
    )
    ASR_DEVICE: str = "cuda"
    ASR_COMPUTE_TYPE: str = "float16"  # int8 on CPU
    ASR_BATCH_SIZE: int = 8

    # Lane B (phonetic lane), on by default since B6b showed against hand labels that it repairs
    # Lane A's false errors. PHONETIC_ENABLED=false runs Lane A alone and loads no phoneme model.
    PHONETIC_ENABLED: bool = True

    # Stage 0 passage preparation (espeak-ng).
    ESPEAK_LANGUAGE: str = "en-us"  # espeak voice
    # Path to libespeak-ng.dll/.so. When set, it is exported as PHONEMIZER_ESPEAK_LIBRARY
    # before the espeak backend is created.
    ESPEAK_LIBRARY: Path | None = None
    PASSAGE_CACHE_SIZE: int = 128  # prepared passages kept in memory

    # Phoneme recogniser (B2). The model and fp16 defaults are set from the spike.
    PHONEME_MODEL: str = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"  # Hugging Face id or local path
    PHONEME_DEVICE: str = "auto"  # auto (CUDA if available), cuda, cpu
    PHONEME_FP16: bool = False  # ignored on CPU
    PHONEME_CHUNK_S: float = 20  # audio per forward pass
    PHONEME_CONTEXT_S: float = 2  # extra audio each side of a chunk; its frames are discarded

    # Join by time (B3, B6a). Values chosen by the B6a sweep (plans/phase-B6a.md).
    JOIN_MAX_REACH_MS: int = 150  # furthest a unit's window reaches into a gap or past either end
    COVERAGE_MIN_MS: int = 120
    COVERAGE_MAX_GAP_MS: int = 300  # silence that ends a coverage run

    # Arbitration (B5, B6a). The guard, short-word and adaptive values come from the B6a sweep;
    # OVERRULE_MATCH, T_FLAG and T_FLAG_INCLUSIVE come from the B6b label sweep
    # (plans/phase-B6b.md).
    COVERAGE_GUARD_RATIO: float = (
        1.0  # CORRECT is overruled only if >= this share of phonemes heard
    )
    SHORT_WORD_MAX_PHONES: int = 3  # CORRECT words this short are never overruled (0 = off)
    ADAPTIVE_OVERRULE: bool = (
        True  # longer overrule threshold for short words with one extra phoneme
    )
    T_FLAG: float = 0.25
    # true: a distance equal to T_FLAG clears a row (<=); false: it keeps the error (<)
    T_FLAG_INCLUSIVE: bool = False
    OVERRULE_MATCH: bool = False  # false: Lane B never changes a row Lane A tagged match
    T_OVERRULE: float = 0.45

    # Worker HTTP API (audio.worker, Phase I2).
    WORKER_MAX_CONCURRENT_JOBS: int = Field(default=1, ge=1)  # jobs on the GPU at once
    WORKER_ALLOWED_MEDIA_DIR: Path = Path("/scratch")  # wav_path must resolve inside it
    WORKER_STATS_WINDOW: int = Field(default=20, ge=1)  # jobs in the rolling average
    WORKER_DISCONNECT_POLL_S: float = Field(default=0.5, gt=0)
    WORKER_LOG_LEVEL: str = "INFO"

    # Audio flags (Phase F1): is_word_by_word, is_disregard_punctuation, is_monotonous,
    # is_hardly_audible. All provisional until checked on real recordings (plans/phase-F1.md).
    FLAGS_ENABLED: bool = True  # false: all four flags are false
    WBW_GAP_MS: int = Field(default=250, ge=0)  # a within-phrase gap this long is a pause
    WBW_MIN_SHARE: float = Field(default=0.5, ge=0, le=1)  # share of pauses that makes it choppy
    PUNCT_SENTENCE_PAUSE_MS: int = Field(default=300, ge=0)
    PUNCT_COMMA_PAUSE_MS: int = Field(default=150, ge=0)
    PUNCT_REL: float = Field(default=1.5, ge=0)  # and this times the median within-phrase gap
    PUNCT_MIN_SHARE: float = Field(default=0.5, ge=0, le=1)  # honoured share below this: flag
    MONO_MAX_ST: float = Field(default=2.0, gt=0)  # pitch SD (semitones) below this: flat
    PITCH_FLOOR_HZ: float = Field(default=75, gt=0)
    PITCH_CEIL_HZ: float = Field(default=600, gt=0)
    AUDIBLE_MIN_DBFS: float = -35  # speech level below this: hardly audible
    AUDIBLE_MIN_SNR_DB: float = 10  # speech minus noise below this: hardly audible
    FLAGS_MIN_GAPS: int = Field(default=10, ge=1)  # within-phrase gaps needed for word-by-word
    FLAGS_MIN_BOUNDARIES: int = Field(default=3, ge=1)  # sentence ends needed for punctuation
    FLAGS_MIN_WORDS: int = Field(default=15, ge=1)  # voiced words needed for monotony

    @model_validator(mode="after")
    def _check_thresholds(self) -> Self:
        if not 0 <= self.T_FLAG < self.T_OVERRULE:
            raise ValueError("thresholds must satisfy 0 <= T_FLAG < T_OVERRULE")
        return self


@lru_cache
def get_audio_settings() -> AudioSettings:
    """Cached settings — .env read once."""
    return AudioSettings()
