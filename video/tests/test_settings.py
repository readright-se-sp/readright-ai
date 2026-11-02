from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults():
    """Criterion 15."""
    settings = Settings(_env_file=None)
    assert settings.log_level == "INFO"
    assert settings.allowed_media_dir is None
    assert settings.max_concurrent_jobs == 2
    assert settings.stats_window == 20
    assert settings.disconnect_poll_seconds == 0.5


def test_reads_values_from_prefixed_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Criterion 15."""
    monkeypatch.setenv("MAI_VIDEO_MAX_CONCURRENT_JOBS", "4")
    monkeypatch.setenv("MAI_VIDEO_ALLOWED_MEDIA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.max_concurrent_jobs == 4
    assert settings.allowed_media_dir == tmp_path


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAI_VIDEO_MAX_CONCURRENT_JOBS", "0"),
        ("MAI_VIDEO_MAX_CONCURRENT_JOBS", "-1"),
        ("MAI_VIDEO_STATS_WINDOW", "0"),
        ("MAI_VIDEO_DISCONNECT_POLL_SECONDS", "0"),
        ("MAI_VIDEO_DISCONNECT_POLL_SECONDS", "-0.5"),
    ],
)
def test_rejects_invalid_values_at_construction(monkeypatch, name, value):
    """Criterion 15."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_env_var_overrides_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Criterion 15."""
    env_file = tmp_path / ".env"
    env_file.write_text("MAI_VIDEO_STATS_WINDOW=5\nMAI_VIDEO_MAX_CONCURRENT_JOBS=3\nUNRELATED=1\n")
    from_file = Settings(_env_file=env_file)
    assert from_file.stats_window == 5
    assert from_file.max_concurrent_jobs == 3

    monkeypatch.setenv("MAI_VIDEO_STATS_WINDOW", "7")
    overridden = Settings(_env_file=env_file)
    assert overridden.stats_window == 7
    assert overridden.max_concurrent_jobs == 3
