import asyncio

from video_helpers import (
    FakeAnalyzer,
    make_client,
    make_job,
    make_settings,
    never_disconnected,
    valid_body,
)

from app.errors import LaneError
from app.jobs import JobRunner
from app.models import ErrorCode


def test_reports_empty_stats_before_any_job(client):
    """Criterion 10."""
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json() == {"queue_length": 0, "avg_processing_seconds": None}


def test_reports_average_after_successful_jobs(settings, webm_file):
    """Criterion 10."""
    analyzer = FakeAnalyzer(delay=0.05)
    with make_client(settings, analyzer) as client:
        for _ in range(2):
            assert client.post("/v1/jobs", json=valid_body(webm_file)).status_code == 200
        stats = client.get("/stats").json()
    assert stats["queue_length"] == 0
    assert isinstance(stats["avg_processing_seconds"], float)
    assert stats["avg_processing_seconds"] >= 0.04


def test_ignores_failed_jobs_in_stats_over_http(settings, webm_file):
    """Criterion 10."""
    analyzer = FakeAnalyzer(analyze_error=RuntimeError("boom"))
    with make_client(settings, analyzer) as client:
        assert client.post("/v1/jobs", json=valid_body(webm_file)).status_code == 500
        stats = client.get("/stats").json()
    assert stats == {"queue_length": 0, "avg_processing_seconds": None}


def test_averages_only_last_window_of_successful_jobs(webm_file):
    """Criterion 10."""
    settings = make_settings(stats_window=2)
    analyzer = FakeAnalyzer()
    runner = JobRunner(settings, analyzer)

    async def scenario() -> float | None:
        for index, delay in enumerate([0.4, 0.0, 0.0]):
            analyzer.delay = delay
            await runner.run(make_job(webm_file, f"job-{index}"), never_disconnected)
        return runner.avg_processing_seconds

    average = asyncio.run(asyncio.wait_for(scenario(), 10))
    # The 0.4 s job has left the window of two; a mean over all three would be ~0.13 s.
    assert average is not None
    assert average < 0.1


def test_failed_jobs_do_not_enter_the_average(webm_file):
    """Criterion 10."""
    settings = make_settings()
    analyzer = FakeAnalyzer()
    runner = JobRunner(settings, analyzer)

    async def scenario() -> float | None:
        assert runner.avg_processing_seconds is None
        analyzer.delay = 0.5
        analyzer.analyze_error = LaneError(ErrorCode.MEDIA_UNREADABLE, "bad")
        try:
            await runner.run(make_job(webm_file, "failing"), never_disconnected)
        except LaneError:
            pass
        assert runner.avg_processing_seconds is None
        analyzer.delay = 0.0
        analyzer.analyze_error = None
        await runner.run(make_job(webm_file, "ok"), never_disconnected)
        return runner.avg_processing_seconds

    average = asyncio.run(asyncio.wait_for(scenario(), 10))
    # Counting the failed 0.5 s run would give a mean of ~0.25 s.
    assert average is not None
    assert average < 0.2
