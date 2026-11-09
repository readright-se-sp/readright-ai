"""Load test: percentile maths and per-status accounting against a local ASGI app."""

import asyncio
import time
import uuid

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from devtools.load_test import percentiles, run_load

STATS = {"completed": 3, "p95_ms": 12}


def test_percentiles_use_linear_interpolation() -> None:
    """Criterion 5."""
    p50, p95, longest = percentiles([10.0, 1.0, 4.0, 2.0, 3.0])
    assert p50 == pytest.approx(3.0)
    assert p95 == pytest.approx(8.8)
    assert longest == pytest.approx(10.0)


def test_percentiles_match_numpy_on_an_uneven_sample() -> None:
    """Criterion 5."""
    times = [0.4, 0.9, 1.1, 1.3, 2.6, 2.7, 5.0]
    p50, p95, longest = percentiles(times)
    assert p50 == pytest.approx(float(np.percentile(times, 50)))
    assert p95 == pytest.approx(float(np.percentile(times, 95)))
    assert longest == 5.0


def test_percentiles_of_one_sample_are_that_sample() -> None:
    """Criterion 5."""
    assert percentiles([2.5]) == pytest.approx((2.5, 2.5, 2.5))


def test_percentiles_of_two_samples_interpolate() -> None:
    """Criterion 5."""
    assert percentiles([1.0, 3.0]) == pytest.approx((2.0, 2.9, 3.0))


def test_percentiles_of_no_samples_raise() -> None:
    """Criterion 5."""
    with pytest.raises(ValueError):
        percentiles([])


def make_lane() -> tuple[FastAPI, list[dict], dict[str, int]]:
    app = FastAPI()
    received: list[dict] = []
    inflight = {"now": 0, "peak": 0}

    @app.post("/v1/jobs")
    async def jobs(request: Request) -> JSONResponse:
        body = await request.json()
        received.append(body)
        inflight["now"] += 1
        inflight["peak"] = max(inflight["peak"], inflight["now"])
        await asyncio.sleep(0.1)
        inflight["now"] -= 1
        path = body["webm_path"]
        if path.startswith("busy"):
            return JSONResponse({"error": "busy"}, status_code=429)
        if path.startswith("broken"):
            return JSONResponse({"error": "boom"}, status_code=500)
        return JSONResponse({"is_finger_pointing": False, "is_lack_expression": False})

    @app.get("/stats")
    async def stats() -> dict:
        return STATS

    return app, received, inflight


def load(paths: list[str], n: int, deadline_s: float = 115):
    app, received, inflight = make_lane()

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport) as client:
            return await run_load("http://lane", paths, n, deadline_s, client=client)

    return asyncio.run(go()), received, inflight


def test_counts_non_200_responses_by_status_and_excludes_them_from_times() -> None:
    """Criterion 5."""
    report, _, _ = load(["ok.webm", "busy.webm", "broken.webm"], n=6)
    assert len(report.ok_times) == 2
    assert report.errors == {429: 2, 500: 2}
    assert all(t > 0 for t in report.ok_times)


def test_records_the_stats_endpoint_json() -> None:
    """Criterion 5."""
    report, _, _ = load(["ok.webm"], n=2)
    assert report.stats == STATS
    assert len(report.ok_times) == 2
    assert report.errors == {}


def test_sends_n_concurrent_jobs_with_cycling_paths_and_a_deadline() -> None:
    """Criterion 5."""
    before = int(time.time() * 1000)
    _, received, inflight = load(["ok1.webm", "ok2.webm"], n=4, deadline_s=60)
    after = int(time.time() * 1000)
    assert inflight["peak"] == 4
    paths = sorted(b["webm_path"] for b in received)
    assert paths == ["ok1.webm", "ok1.webm", "ok2.webm", "ok2.webm"]
    ids = {b["job_id"] for b in received}
    assert len(ids) == 4
    for job_id in ids:
        uuid.UUID(job_id)
    for body in received:
        assert before + 60_000 <= body["deadline"] <= after + 60_000


def test_a_run_with_only_failures_has_no_successful_times() -> None:
    """Criterion 5."""
    report, _, _ = load(["busy.webm"], n=3)
    assert report.ok_times == []
    assert report.errors == {429: 3}
    with pytest.raises(ValueError):
        percentiles(report.ok_times)
