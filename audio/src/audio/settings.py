from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUDIO_", env_file=".env", extra="ignore")

    log_level: str = "INFO"
    data_dir: Path = Path("data")  # local recordings (git-ignored), used by devtools later
    espeak_language: str = "en-us"  # espeak voice
    # Path to libespeak-ng.dll/.so. When set, it is exported as PHONEMIZER_ESPEAK_LIBRARY
    # before the espeak backend is created.
    espeak_library: Path | None = None
    passage_cache_size: int = 128  # prepared passages kept in memory

    # Phoneme recogniser (B2). The model and fp16 defaults are set from the spike.
    phoneme_model: str = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"  # Hugging Face id or local path
    phoneme_device: str = "auto"  # auto (CUDA if available), cuda, cpu
    phoneme_fp16: bool = False  # ignored on CPU
    phoneme_chunk_s: float = 20  # audio per forward pass
    phoneme_context_s: float = 2  # extra audio each side of a chunk; its frames are discarded

    # Join by time (B3). Starting values; B6 measures them.
    join_pad_ms: int = 80  # AUDIO_JOIN_PAD_MS, try 50-100
    coverage_min_ms: int = 120  # AUDIO_COVERAGE_MIN_MS
    coverage_max_gap_ms: int = 300  # AUDIO_COVERAGE_MAX_GAP_MS, silence that ends a run
