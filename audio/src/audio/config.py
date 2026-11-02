from functools import lru_cache

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


@lru_cache
def get_audio_settings() -> AudioSettings:
    """Cached settings — .env read once."""
    return AudioSettings()
