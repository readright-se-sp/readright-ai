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
