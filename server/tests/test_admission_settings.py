"""Admission and readiness settings."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides):
    return Settings(_env_file=None, api_key="test-key", **overrides)  # type: ignore[call-arg]


def test_admission_settings_defaults():
    """Criterion 1."""
    settings = make()
    assert settings.max_in_flight == 10
    assert settings.stage_estimate_download_seconds == 15.0
    assert settings.stage_estimate_upload_seconds == 10.0
    assert settings.stage_estimate_lane_seconds == 75.0
    assert settings.stats_cache_seconds == 1.0
    assert settings.dependency_timeout_seconds == 2.0


def test_admission_settings_read_mai_prefixed_variables(monkeypatch):
    """Criterion 1."""
    monkeypatch.setenv("MAI_MAX_IN_FLIGHT", "3")
    monkeypatch.setenv("MAI_STAGE_ESTIMATE_DOWNLOAD_SECONDS", "1.5")
    monkeypatch.setenv("MAI_STAGE_ESTIMATE_UPLOAD_SECONDS", "2.5")
    monkeypatch.setenv("MAI_STAGE_ESTIMATE_LANE_SECONDS", "3.5")
    monkeypatch.setenv("MAI_STATS_CACHE_SECONDS", "0.25")
    monkeypatch.setenv("MAI_DEPENDENCY_TIMEOUT_SECONDS", "4.5")
    settings = make()
    assert settings.max_in_flight == 3
    assert settings.stage_estimate_download_seconds == 1.5
    assert settings.stage_estimate_upload_seconds == 2.5
    assert settings.stage_estimate_lane_seconds == 3.5
    assert settings.stats_cache_seconds == 0.25
    assert settings.dependency_timeout_seconds == 4.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_in_flight": 0},
        {"max_in_flight": -1},
        {"stage_estimate_download_seconds": -1},
        {"stage_estimate_upload_seconds": -0.1},
        {"stage_estimate_lane_seconds": 0},
        {"stage_estimate_lane_seconds": -5},
        {"stats_cache_seconds": -1},
        {"dependency_timeout_seconds": 0},
        {"dependency_timeout_seconds": -2},
    ],
)
def test_invalid_admission_settings_are_rejected(overrides):
    """Criterion 1."""
    with pytest.raises(ValidationError):
        make(**overrides)


def test_zero_is_allowed_where_non_negative():
    """Criterion 1."""
    settings = make(
        stage_estimate_download_seconds=0, stage_estimate_upload_seconds=0, stats_cache_seconds=0
    )
    assert settings.stats_cache_seconds == 0
