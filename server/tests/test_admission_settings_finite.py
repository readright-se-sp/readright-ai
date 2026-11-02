"""The new float settings reject inf and nan."""

import pytest
from pydantic import ValidationError

from app.config import Settings

FIELDS = [
    "stage_estimate_download_seconds",
    "stage_estimate_upload_seconds",
    "stage_estimate_lane_seconds",
    "stats_cache_seconds",
    "dependency_timeout_seconds",
]
BAD = ["inf", "-inf", "nan", "Infinity"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", BAD)
def test_non_finite_values_from_env_are_rejected(monkeypatch, field, value):
    """Criterion 23."""
    monkeypatch.setenv(f"MAI_{field.upper()}", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="test-key")  # type: ignore[call-arg]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_non_finite_values_from_arguments_are_rejected(field, value):
    """Criterion 23."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="test-key", **{field: value})  # type: ignore[call-arg]
