import logging

import pytest
from pydantic import ValidationError

from app.config import Settings, api_key_source, env_file_overrides
from app.main import create_app

FILE_KEY = "file-key-aaaa"
ENV_KEY = "env-key-bbbb"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A `.env` holding FILE_KEY in a temp folder, which becomes the working directory."""
    (tmp_path / ".env").write_text(f"MAI_API_KEY={FILE_KEY}\nMAI_LOG_LEVEL=DEBUG\n")
    monkeypatch.chdir(tmp_path)


def test_key_from_env_file(env_file):
    assert Settings().api_key.get_secret_value() == FILE_KEY  # type: ignore[call-arg]
    assert api_key_source() == ".env"
    assert env_file_overrides() == []


def test_env_var_overrides_env_file(env_file, monkeypatch):
    monkeypatch.setenv("MAI_API_KEY", ENV_KEY)
    assert Settings().api_key.get_secret_value() == ENV_KEY  # type: ignore[call-arg]
    assert api_key_source() == "environment"
    assert env_file_overrides() == ["MAI_API_KEY"]


def test_same_value_is_not_an_override(env_file, monkeypatch):
    monkeypatch.setenv("MAI_API_KEY", FILE_KEY)
    assert env_file_overrides() == []


def test_override_names_are_case_insensitive(env_file, monkeypatch):
    # Only meaningful where env names are case-sensitive, but must not break elsewhere.
    monkeypatch.setenv("MAI_LOG_LEVEL", "ERROR")
    assert env_file_overrides() == ["MAI_LOG_LEVEL"]


def test_no_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAI_API_KEY", ENV_KEY)
    assert env_file_overrides() == []
    assert env_file_overrides(tmp_path / "missing.env") == []


@pytest.mark.parametrize("key", ["", "   ", "change-me", "  change-me "])
def test_bad_keys_are_rejected(key):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key=key)  # type: ignore[call-arg]


def test_key_is_stripped():
    settings = Settings(_env_file=None, api_key="  abc  ")  # type: ignore[call-arg]
    assert settings.api_key.get_secret_value() == "abc"


def test_empty_env_var_stops_startup(env_file, monkeypatch):
    monkeypatch.setenv("MAI_API_KEY", "")
    with pytest.raises(ValidationError):
        create_app()


def test_create_app_logs_override_without_values(env_file, monkeypatch, caplog):
    monkeypatch.setenv("MAI_API_KEY", ENV_KEY)
    # configure_logging() replaces the root handlers, so attach caplog's directly.
    config_logger = logging.getLogger("app.config")
    config_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="app.config"):
            create_app()
    finally:
        config_logger.removeHandler(caplog.handler)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [(r.getMessage(), r.setting) for r in warnings] == [  # type: ignore[attr-defined]
        ("environment variable overrides .env", "MAI_API_KEY")
    ]
    loaded = [r for r in caplog.records if r.getMessage() == "api key loaded"]
    assert [r.source for r in loaded] == ["environment"]  # type: ignore[attr-defined]
    assert FILE_KEY not in caplog.text
    assert ENV_KEY not in caplog.text
