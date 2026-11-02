from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioSettings(BaseSettings):
    """Audio service config. Env vars are prefixed AUDIO_ (e.g. AUDIO_ASR_MODEL)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AUDIO_",
        extra="ignore",
    )

    ASR_MODEL: str = "models/crisper2_large_ct2"  # CTranslate2 conversion of nyralabs/CrisperWhisper2.0_large
    ASR_DEVICE: str = "cuda"
    ASR_COMPUTE_TYPE: str = "float16"  # int8 on CPU
    ASR_BATCH_SIZE: int = 8

    # Lane B (phonetic lane). PHONETIC_ENABLED=false runs Lane A alone (no phoneme model is loaded).
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

    # Join by time (B3). Starting values; B6 measures them.
    JOIN_MODE: Literal["midpoint", "pad"] = "midpoint"  # window rule, see lane_b/join.py
    JOIN_PAD_MS: int = 50  # pad mode only
    JOIN_MAX_REACH_MS: int = 200  # midpoint mode: furthest a unit reaches into a gap or past ends
    COVERAGE_MIN_MS: int = 120
    COVERAGE_MAX_GAP_MS: int = 300  # silence that ends a coverage run

    # Arbitration (B5). Starting values; B6 tunes them (spec section 9: measure, do not guess).
    COVERAGE_GUARD_RATIO: float = 0.5  # CORRECT is overruled only if >= this share of phonemes heard
    ADAPTIVE_OVERRULE: bool = True  # longer overrule threshold for short words with one extra phoneme
    T_FLAG: float = 0.25
    T_OVERRULE: float = 0.45

    @model_validator(mode="after")
    def _check_thresholds(self) -> Self:
        if not 0 <= self.T_FLAG < self.T_OVERRULE:
            raise ValueError("thresholds must satisfy 0 <= T_FLAG < T_OVERRULE")
        return self


@lru_cache
def get_audio_settings() -> AudioSettings:
    """Cached settings — .env read once."""
    return AudioSettings()
