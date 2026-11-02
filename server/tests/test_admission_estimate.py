"""Finish-time estimate, Retry-After arithmetic and the worker stats model."""

import math

import pytest
from pydantic import ValidationError

from app.admission import WorkerStats, estimate_finish_seconds, retry_after_seconds
from app.config import Settings


@pytest.fixture
def default_settings() -> Settings:
    return Settings(_env_file=None, api_key="test-key")  # type: ignore[call-arg]


def stats(queue: int = 0, avg: float | None = None) -> WorkerStats:
    return WorkerStats(queue_length=queue, avg_processing_seconds=avg)


def test_worker_stats_accepts_valid_values():
    """Criterion 2."""
    assert stats(0, None).avg_processing_seconds is None
    assert stats(3, 1.2).queue_length == 3
    assert stats(0, 0).avg_processing_seconds == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"queue_length": -1, "avg_processing_seconds": 1.0},
        {"queue_length": 0, "avg_processing_seconds": -0.5},
        {"queue_length": 0, "avg_processing_seconds": math.inf},
        {"queue_length": 0, "avg_processing_seconds": math.nan},
        {"queue_length": "many", "avg_processing_seconds": 1.0},
        {"avg_processing_seconds": 1.0},
    ],
)
def test_worker_stats_rejects_invalid_values(payload):
    """Criterion 2."""
    with pytest.raises(ValidationError):
        WorkerStats.model_validate(payload)


def test_estimate_sums_download_slowest_lane_and_upload(default_settings):
    """Criterion 3: 15 + (2+1)*10 + 10."""
    assert estimate_finish_seconds(stats(2, 10), stats(0, 5), 0, default_settings) == 55


def test_estimate_uses_requests_in_flight_as_queue_floor(default_settings):
    """Criterion 3: 15 + (4+1)*10 + 10."""
    assert estimate_finish_seconds(stats(2, 10), stats(0, 5), 4, default_settings) == 75


def test_estimate_prefers_worker_queue_when_longer_than_in_flight(default_settings):
    """Criterion 3: q = max(queue, others), not their sum."""
    assert estimate_finish_seconds(stats(5, 10), stats(0, 5), 2, default_settings) == 85


def test_estimate_applies_in_flight_floor_to_the_other_lane_too(default_settings):
    """Criterion 3: video q becomes 3, so (3+1)*30 dominates."""
    assert estimate_finish_seconds(stats(0, 1), stats(0, 30), 3, default_settings) == 145


def test_estimate_uses_lane_default_when_average_is_unknown(default_settings):
    """Criterion 3."""
    assert estimate_finish_seconds(stats(0, None), stats(0, None), 0, default_settings) == 100
    assert estimate_finish_seconds(stats(1, None), stats(0, 5), 0, default_settings) == 175


def test_estimate_follows_configured_stage_values():
    """Criterion 3."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        api_key="test-key",
        stage_estimate_download_seconds=1,
        stage_estimate_upload_seconds=2,
        stage_estimate_lane_seconds=3,
    )
    assert estimate_finish_seconds(stats(0, None), stats(0, None), 0, settings) == 6
    assert estimate_finish_seconds(stats(1, None), stats(0, None), 0, settings) == 9


@pytest.mark.parametrize(
    ("estimate", "remaining", "expected"),
    [
        (65.0, 60.0, 5),
        (65.2, 60.0, 6),
        (60.1, 60.0, 1),
        (50.0, 60.0, 1),
        (60.0, 60.0, 1),
        (200.0, 0.0, 200),
    ],
)
def test_retry_after_is_ceiling_of_overshoot_at_least_one(estimate, remaining, expected):
    """Criterion 4."""
    assert retry_after_seconds(estimate, remaining) == expected
