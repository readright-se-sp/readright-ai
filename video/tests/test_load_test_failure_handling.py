"""Load test survives transport failures and reports a meaningful exit code."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from devtools import load_test
from devtools.load_test import run_load


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/stats":
        raise httpx.ConnectError("stats down")
    if b"flaky" in request.content:
        raise httpx.ConnectError("connection refused")
    return httpx.Response(200, json={"is_finger_pointing": False})


def run(paths: list[str], n: int, mock=handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            return await run_load("http://lane", paths, n, 115, client=client)

    return asyncio.run(go())


def test_counts_httpx_exceptions_under_status_zero_and_keeps_other_timings() -> None:
    """Criterion 8."""
    report = run(["ok.webm", "flaky.webm"], n=6)
    assert report.errors == {0: 3}
    assert len(report.ok_times) == 3


def test_stats_is_none_when_the_stats_request_fails() -> None:
    """Criterion 8."""
    report = run(["ok.webm"], n=2)
    assert report.stats is None
    assert len(report.ok_times) == 2


def fake_run_load(ok_times: list[float], errors: dict[int, int]):
    async def fake(url, paths, n, deadline_s, client=None):
        return SimpleNamespace(ok_times=ok_times, errors=errors, stats={})

    return fake


def test_main_exits_1_when_any_job_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 8."""
    monkeypatch.setattr(load_test, "run_load", fake_run_load([1.0, 2.0], {500: 1}))
    assert load_test.main(["http://lane", "a.webm", "-n", "3"]) == 1


def test_main_exits_1_when_no_job_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 8."""
    monkeypatch.setattr(load_test, "run_load", fake_run_load([], {}))
    assert load_test.main(["http://lane", "a.webm", "-n", "3"]) == 1


def test_main_exits_0_when_every_job_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 8."""
    monkeypatch.setattr(load_test, "run_load", fake_run_load([1.0, 2.0, 3.0], {}))
    assert load_test.main(["http://lane", "a.webm", "-n", "3"]) == 0


def test_main_exits_1_when_the_lane_is_unreachable() -> None:
    """Criterion 8."""
    assert load_test.main(["http://127.0.0.1:1", "a.webm", "-n", "2", "--deadline", "5"]) == 1
