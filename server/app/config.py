import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr, field_validator
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
