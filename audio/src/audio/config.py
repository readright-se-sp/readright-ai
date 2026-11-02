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

    # Lane B (phonetic lane). PHONETIC_ENABLED=false (the default until B6b shows Lane B beats
    # Lane A against teacher labels) runs Lane A alone and loads no phoneme model.
    PHONETIC_ENABLED: bool = False

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
    # T_FLAG and T_OVERRULE are still starting values for B6b (needs labels).
    COVERAGE_GUARD_RATIO: float = (
        1.0  # CORRECT is overruled only if >= this share of phonemes heard
    )
    SHORT_WORD_MAX_PHONES: int = 3  # CORRECT words this short are never overruled (0 = off)
    ADAPTIVE_OVERRULE: bool = (
        True  # longer overrule threshold for short words with one extra phoneme
    )
    T_FLAG: float = 0.25
    T_OVERRULE: float = 0.45

    # Worker HTTP API (audio.worker, Phase I2).
    WORKER_MAX_CONCURRENT_JOBS: int = Field(default=1, ge=1)  # jobs on the GPU at once
    WORKER_ALLOWED_MEDIA_DIR: Path = Path("/scratch")  # wav_path must resolve inside it
    WORKER_STATS_WINDOW: int = Field(default=20, ge=1)  # jobs in the rolling average
    WORKER_DISCONNECT_POLL_S: float = Field(default=0.5, gt=0)
    WORKER_LOG_LEVEL: str = "INFO"

    @model_validator(mode="after")
    def _check_thresholds(self) -> Self:
        if not 0 <= self.T_FLAG < self.T_OVERRULE:
            raise ValueError("thresholds must satisfy 0 <= T_FLAG < T_OVERRULE")
        return self


@lru_cache
def get_audio_settings() -> AudioSettings:
    """Cached settings — .env read once."""
    return AudioSettings()
