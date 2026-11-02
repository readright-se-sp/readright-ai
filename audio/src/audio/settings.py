from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUDIO_", env_file=".env", extra="ignore")

    log_level: str = "INFO"
    data_dir: Path = Path("data")  # local recordings (git-ignored), used by devtools later
