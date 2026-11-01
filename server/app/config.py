import os
from pathlib import Path
from typing import Literal, Self

from dotenv import dotenv_values
from pydantic import PositiveFloat, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_KEY_PLACEHOLDER = "change-me"  # value in .env.example
_ENV_PREFIX = "MAI_"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=_ENV_PREFIX, env_file=".env", extra="ignore")

    api_key: SecretStr
    log_level: str = "INFO"
    request_deadline_seconds: float = 115.0
    allow_audio_only_on_video_failure: bool = False
    default_output_key_prefix: str = "analyses/{request_id}/miscues/"
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")  # may hold a password
    in_progress_ttl_seconds: int = 130
    result_ttl_seconds: PositiveInt = 604800
    redis_timeout_seconds: PositiveFloat = 2.0
    pipeline_mode: Literal["stub", "real"] = "real"
    scratch_dir: Path | None = None
    max_video_bytes: PositiveInt = 209_715_200  # 200 MiB
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if not key:
            raise ValueError("MAI_API_KEY must not be empty")
        if key == API_KEY_PLACEHOLDER:
            raise ValueError(
                "MAI_API_KEY is still the placeholder from .env.example; set a real key"
            )
        return SecretStr(key)

    @model_validator(mode="after")
    def _check_lease_outlives_deadline(self) -> Self:
        # A lease that expires mid-request would let a retry start a second run.
        if self.in_progress_ttl_seconds <= self.request_deadline_seconds:
            raise ValueError(
                "MAI_IN_PROGRESS_TTL_SECONDS must be greater than MAI_REQUEST_DEADLINE_SECONDS"
            )
        return self


def env_file_overrides(env_file: str | Path = ".env") -> list[str]:
    """MAI_* names set in `env_file` but overridden by a different value in the environment."""
    # Windows env var names are case-insensitive, so compare upper-cased.
    environ = {name.upper(): value for name, value in os.environ.items()}
    overridden = set()
    for name, file_value in dotenv_values(env_file).items():
        upper = name.upper()
        if upper.startswith(_ENV_PREFIX) and upper in environ and environ[upper] != file_value:
            overridden.add(upper)
    return sorted(overridden)


def api_key_source() -> str:
    """Where MAI_API_KEY comes from: "environment" or ".env". Never the value."""
    in_environment = any(name.upper() == f"{_ENV_PREFIX}API_KEY" for name in os.environ)
    return "environment" if in_environment else ".env"
