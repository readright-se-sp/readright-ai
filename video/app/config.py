from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAI_VIDEO_", env_file=".env", extra="ignore")

    log_level: str = "INFO"
    allowed_media_dir: Path | None = None
    max_concurrent_jobs: int = Field(default=2, ge=1)
    stats_window: int = Field(default=20, ge=1)
    disconnect_poll_seconds: float = Field(default=0.5, gt=0)
