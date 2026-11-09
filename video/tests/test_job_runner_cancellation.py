import asyncio
import threading
import time

import pytest
from video_helpers import FakeAnalyzer, make_job, make_settings, never_disconnected

from app.analyzer import JobCancelled
from app.jobs import JobRunner


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


def test_task_cancellation_sets_cancel_event_and_keeps_slot_until_thread_ends(webm_file):
    """Criterion 18."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate)
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)

    async def scenario():
        first = asyncio.create_task(runner.run(make_job(webm_file, "first"), never_disconnected))
        assert await until(lambda: analyzer.active == 1)
        ctx = analyzer.calls[0][1]
        second = asyncio.create_task(runner.run(make_job(webm_file, "second"), never_disconnected))
        assert await until(lambda: runner.queue_length == 1)

        first.cancel()
        assert await until(ctx.cancel_event.is_set)
        await asyncio.sleep(0.3)
        # The blocked thread still holds the only slot.
        assert analyzer.active == 1
        assert len(analyzer.calls) == 1
        assert analyzer.max_active == 1

        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert [c[1].job_id for c in analyzer.calls] == ["first", "second"]
        assert analyzer.max_active == 1
        assert runner.queue_length == 0

    run_scenario(scenario, [gate])


def test_disconnect_already_true_when_slot_frees_raises_cancelled_without_analyzing(webm_file):
    """Criterion 20."""
    analyzer = FakeAnalyzer()
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)

    async def disconnected() -> bool:
        return True

    async def scenario():
        with pytest.raises(JobCancelled):
            await runner.run(make_job(webm_file, "gone"), disconnected)
        assert analyzer.calls == []
        assert runner.queue_length == 0
        result = await runner.run(make_job(webm_file, "next"), never_disconnected)
        assert result.is_finger_pointing is False
        assert [c[1].job_id for c in analyzer.calls] == ["next"]

    run_scenario(scenario, [])


def test_queued_job_disconnected_before_slot_frees_never_analyzes(webm_file):
    """Criterion 20."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(analyze_gate=gate)
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)
    state = {"gone": False}

    async def is_disconnected() -> bool:
        return state["gone"]

    async def scenario():
        first = asyncio.create_task(runner.run(make_job(webm_file, "first"), never_disconnected))
        assert await until(lambda: analyzer.active == 1)
        second = asyncio.create_task(runner.run(make_job(webm_file, "second"), is_disconnected))
        assert await until(lambda: runner.queue_length == 1)
        state["gone"] = True
        await asyncio.sleep(0.3)
        gate.set()
        await first
        with pytest.raises(JobCancelled):
            await second
        assert [c[1].job_id for c in analyzer.calls] == ["first"]
        assert runner.queue_length == 0
        again = await runner.run(make_job(webm_file, "third"), never_disconnected)
        assert again.is_lack_expression is False

    run_scenario(scenario, [gate])


def test_failing_disconnect_check_is_treated_as_disconnect(webm_file):
    """Criterion 20."""
    analyzer = FakeAnalyzer()
    runner = JobRunner(make_settings(max_concurrent_jobs=1), analyzer)
    unhandled: list[dict] = []

    async def broken() -> bool:
        raise RuntimeError("probe failed")

    async def scenario():
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: unhandled.append(ctx))
        with pytest.raises(JobCancelled):
            await runner.run(make_job(webm_file, "broken"), broken)
        assert analyzer.calls == []
        assert runner.queue_length == 0
        await runner.run(make_job(webm_file, "next"), never_disconnected)
        assert len(analyzer.calls) == 1
        import gc

        gc.collect()
        await asyncio.sleep(0.1)
        assert unhandled == []

    run_scenario(scenario, [])
