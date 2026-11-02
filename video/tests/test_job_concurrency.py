import asyncio
import threading
import time

import pytest
from video_helpers import FakeAnalyzer, make_job, make_settings, never_disconnected, now_ms

from app.analyzer import JobCancelled
from app.errors import LaneError
from app.jobs import JobRunner
from app.models import ErrorCode


async def until(predicate, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def run_scenario(coro_factory, gates: list[threading.Event]):
    async def guarded():
        try:
            return await asyncio.wait_for(coro_factory(), 10)
        finally:
            for gate in gates:
                gate.set()

    return asyncio.run(guarded())


def test_limits_parallel_analyses_and_counts_waiting_jobs(webm_file):
    """Criterion 11."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate)
    runner = JobRunner(make_settings(max_concurrent_jobs=2), analyzer)

    async def scenario():
        tasks = [
            asyncio.create_task(runner.run(make_job(webm_file, f"job-{i}"), never_disconnected))
            for i in range(4)
        ]
        assert await until(lambda: analyzer.active == 2 and runner.queue_length == 2)
        await asyncio.sleep(0.2)
        assert analyzer.active == 2
        assert runner.queue_length == 2
        gate.set()
        results = await asyncio.gather(*tasks)
        assert [r.is_finger_pointing for r in results] == [False] * 4
        assert analyzer.max_active == 2
        assert len(analyzer.calls) == 4
        assert runner.queue_length == 0

    run_scenario(scenario, [gate])


def test_expired_deadline_while_queued_raises_without_analyzing(webm_file):
    """Criterion 12."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate)
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)

    async def scenario():
        first = asyncio.create_task(runner.run(make_job(webm_file, "first"), never_disconnected))
        assert await until(lambda: analyzer.active == 1)
        short = make_job(webm_file, "second", deadline=now_ms() + 300)
        second = asyncio.create_task(runner.run(short, never_disconnected))
        assert await until(lambda: runner.queue_length == 1)
        await asyncio.sleep(0.5)
        gate.set()
        with pytest.raises(LaneError) as excinfo:
            await second
        assert excinfo.value.code == ErrorCode.DEADLINE_EXCEEDED
        await first
        assert len(analyzer.calls) == 1
        assert analyzer.calls[0][1].job_id == "first"
        assert runner.queue_length == 0

    run_scenario(scenario, [gate])


def test_disconnect_sets_cancel_event_within_two_poll_intervals(webm_file):
    """Criterion 13."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate, cancel_aware=True)
    settings = make_settings(max_concurrent_jobs=1, disconnect_poll_seconds=0.05)
    runner = JobRunner(settings, analyzer)
    state = {"disconnected_at": None}

    async def is_disconnected() -> bool:
        return state["disconnected_at"] is not None

    async def scenario():
        task = asyncio.create_task(runner.run(make_job(webm_file), is_disconnected))
        assert await until(lambda: analyzer.active == 1)
        ctx = analyzer.calls[0][1]
        assert not ctx.cancel_event.is_set()
        state["disconnected_at"] = time.monotonic()
        assert await until(ctx.cancel_event.is_set)
        elapsed = time.monotonic() - state["disconnected_at"]
        # 2 x 0.05 s per the contract, plus generous scheduling margin.
        assert elapsed < 0.5
        try:
            await task
        except JobCancelled:
            pass

    run_scenario(scenario, [gate])


def test_cancelled_job_releases_slot_for_next_job(webm_file):
    """Criterion 13."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate, cancel_aware=True)
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)
    disconnected = {"value": False}

    async def is_disconnected() -> bool:
        return disconnected["value"]

    async def scenario():
        first = asyncio.create_task(runner.run(make_job(webm_file, "first"), is_disconnected))
        assert await until(lambda: analyzer.active == 1)
        disconnected["value"] = True
        try:
            await first
        except JobCancelled:
            pass
        assert await until(lambda: analyzer.active == 0)
        gate.set()
        result = await runner.run(make_job(webm_file, "second"), never_disconnected)
        assert result.is_finger_pointing is False
        assert [c[1].job_id for c in analyzer.calls] == ["first", "second"]

    run_scenario(scenario, [gate])


def test_slot_is_held_until_uncooperative_analyzer_returns(webm_file):
    """Criterion 13."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate, cancel_aware=False)
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)
    disconnected = {"value": False}

    async def is_disconnected() -> bool:
        return disconnected["value"]

    async def scenario():
        first = asyncio.create_task(runner.run(make_job(webm_file, "first"), is_disconnected))
        assert await until(lambda: analyzer.active == 1)
        ctx = analyzer.calls[0][1]
        disconnected["value"] = True
        assert await until(ctx.cancel_event.is_set)
        second = asyncio.create_task(runner.run(make_job(webm_file, "second"), never_disconnected))
        assert await until(lambda: runner.queue_length == 1)
        await asyncio.sleep(0.2)
        # The analyzer ignored the cancel, so its slot is still taken.
        assert len(analyzer.calls) == 1
        gate.set()
        await asyncio.gather(first, second, return_exceptions=True)
        assert [c[1].job_id for c in analyzer.calls] == ["first", "second"]

    run_scenario(scenario, [gate])
