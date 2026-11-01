"""Running the audio and video lanes in parallel and handling their failures (criteria 9-14)."""

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from conftest import valid_body
from lane_helpers import REQUEST_ID, audio_result, video_result, worker_settings

from app import pipeline
from app.deadline import Deadline, deadline_var
from app.errors import MAIError
from app.main import create_app
from app.media import ProbeResult
from app.merge import merge_results
from app.models import AnalysisRequest, ErrorCode
from app.pipeline import pipeline_processor

pytestmark = pytest.mark.anyio

KEY = f"mai:req:{REQUEST_ID}"
GOOD_PROBE = ProbeResult("matroska,webm", True, True, 1.0)


class Lanes:
    """Fake lane coroutines that can wait, fail, or hang while recording what happened."""

    def __init__(self):
        self.audio_started = asyncio.Event()
        self.video_started = asyncio.Event()
        self.audio_cancelled = False
        self.video_cancelled = False
        self.audio_calls = 0
        self.video_calls = 0
        self.audio_behavior = "ok"  # ok | fail | hang | wait_for_video | slow_ok
        self.video_behavior = "ok"  # ok | fail | hang | wait_for_audio
        self.audio_error = MAIError(ErrorCode.NO_SPEECH_DETECTED, "no speech")
        self.video_error = MAIError(ErrorCode.PROCESSING_FAILED, "video broke")

    async def audio(self, body, wav_path, settings, deadline):
        self.audio_calls += 1
        self.audio_started.set()
        try:
            if self.audio_behavior == "hang":
                await asyncio.sleep(30)
            elif self.audio_behavior == "wait_for_video":
                await asyncio.wait_for(self.video_started.wait(), timeout=2)
            elif self.audio_behavior == "fail":
                await asyncio.wait_for(self.video_started.wait(), timeout=2)
                raise self.audio_error
            elif self.audio_behavior == "slow_ok":
                await asyncio.wait_for(self.video_started.wait(), timeout=2)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            self.audio_cancelled = True
            raise
        return audio_result()

    async def video(self, body, webm_path, settings, deadline):
        self.video_calls += 1
        self.video_started.set()
        try:
            if self.video_behavior == "hang":
                await asyncio.sleep(30)
            elif self.video_behavior == "wait_for_audio":
                await asyncio.wait_for(self.audio_started.wait(), timeout=2)
            elif self.video_behavior == "fail":
                await asyncio.wait_for(self.audio_started.wait(), timeout=2)
                raise self.video_error
        except asyncio.CancelledError:
            self.video_cancelled = True
            raise
        return video_result()


@pytest.fixture
def lanes(monkeypatch) -> Lanes:
    fake = Lanes()
    monkeypatch.setattr(pipeline, "run_audio_lane", fake.audio)
    monkeypatch.setattr(pipeline, "run_video_lane", fake.video)

    async def download(url, dest, *, max_bytes, deadline):
        dest.write_bytes(b"video")
        return 5

    async def probe(path, settings):
        return GOOD_PROBE

    async def convert(src, dest, settings):
        dest.write_bytes(b"RIFF")

    monkeypatch.setattr(pipeline, "download_video", download)
    monkeypatch.setattr(pipeline, "probe", probe)
    monkeypatch.setattr(pipeline, "convert_to_wav", convert)
    return fake


def request_body() -> AnalysisRequest:
    return AnalysisRequest(**valid_body())


async def run(tmp_path: Path, **settings_overrides):
    settings = worker_settings(tmp_path, **settings_overrides)
    return await pipeline_processor(request_body(), settings), settings


# 9. lanes get the right files --------------------------------------------------------------


async def test_both_lanes_receive_their_files_and_result_is_merged(tmp_path, monkeypatch, lanes):
    """criterion 9"""
    seen = {}

    async def audio(body, wav_path, settings, deadline):
        seen["audio"] = (wav_path, wav_path.read_bytes())
        return audio_result()

    async def video(body, webm_path, settings, deadline):
        seen["video"] = (webm_path, webm_path.read_bytes())
        return video_result()

    monkeypatch.setattr(pipeline, "run_audio_lane", audio)
    monkeypatch.setattr(pipeline, "run_video_lane", video)

    result, settings = await run(tmp_path)

    work = tmp_path / "scratch" / REQUEST_ID
    assert seen["audio"] == (work / "audio.wav", b"RIFF")
    assert seen["video"] == (work / "input.webm", b"video")
    assert result == merge_results(request_body(), settings, audio_result(), video_result())
    assert not work.exists()


# 10. concurrency ---------------------------------------------------------------------------


async def test_lanes_run_at_the_same_time(tmp_path, lanes):
    """criterion 10: each lane waits for the other to start, so serial calls would deadlock."""
    lanes.audio_behavior = "wait_for_video"
    lanes.video_behavior = "wait_for_audio"
    result, _ = await asyncio.wait_for(run(tmp_path), timeout=5)
    assert result.reading_score == audio_result().reading_score
    assert lanes.audio_calls == lanes.video_calls == 1


# 11. audio failure -------------------------------------------------------------------------


async def test_audio_failure_is_raised_and_video_call_is_cancelled(tmp_path, lanes):
    """criterion 11"""
    lanes.audio_behavior = "fail"
    lanes.video_behavior = "hang"
    with pytest.raises(MAIError) as exc:
        await run(tmp_path)
    assert exc.value.code == ErrorCode.NO_SPEECH_DETECTED
    assert lanes.video_cancelled
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


# 12. video failure, flag off ---------------------------------------------------------------


async def test_video_failure_fails_request_and_cancels_audio_call(tmp_path, lanes):
    """criterion 12"""
    lanes.video_behavior = "fail"
    lanes.audio_behavior = "hang"
    with pytest.raises(MAIError) as exc:
        await run(tmp_path, allow_audio_only_on_video_failure=False)
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.status == 500
    assert lanes.audio_cancelled
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


# 13. video failure, flag on ----------------------------------------------------------------


async def test_video_failure_with_flag_returns_audio_only_result(tmp_path, lanes, log_records):
    """criterion 13: the server waits for the slower audio lane."""
    lanes.video_behavior = "fail"
    lanes.audio_behavior = "slow_ok"
    result, settings = await run(tmp_path, allow_audio_only_on_video_failure=True)

    assert not lanes.audio_cancelled
    assert result == merge_results(request_body(), settings, audio_result(), None)
    assert result.is_finger_pointing is False
    assert result.is_lack_expression is False
    assert result.reading_score == audio_result().reading_score
    warnings = [
        r
        for r in log_records.records
        if r.levelno == logging.WARNING
        and r.getMessage() == "video lane failed; returning audio-only result"
    ]
    assert warnings


async def test_video_timeout_with_flag_still_fails_the_request(tmp_path, lanes):
    """criterion 13: TIMEOUT from the video lane is not downgraded."""
    lanes.video_behavior = "fail"
    lanes.video_error = MAIError(ErrorCode.TIMEOUT, "slow")
    lanes.audio_behavior = "hang"
    with pytest.raises(MAIError) as exc:
        await run(tmp_path, allow_audio_only_on_video_failure=True)
    assert exc.value.code == ErrorCode.TIMEOUT
    assert lanes.audio_cancelled


async def test_audio_failure_with_flag_still_fails_the_request(tmp_path, lanes):
    """criterion 13"""
    lanes.audio_behavior = "fail"
    lanes.video_behavior = "ok"
    with pytest.raises(MAIError) as exc:
        await run(tmp_path, allow_audio_only_on_video_failure=True)
    assert exc.value.code == ErrorCode.NO_SPEECH_DETECTED


async def test_audio_failure_after_video_failure_with_flag_fails_the_request(tmp_path, lanes):
    """criterion 13"""
    lanes.video_behavior = "fail"
    lanes.audio_behavior = "fail"
    lanes.audio_error = MAIError(ErrorCode.PROCESSING_FAILED, "audio broke")
    with pytest.raises(MAIError) as exc:
        await run(tmp_path, allow_audio_only_on_video_failure=True)
    assert exc.value.code == ErrorCode.PROCESSING_FAILED


async def test_lanes_receive_the_request_deadline_from_context(tmp_path, monkeypatch, lanes):
    """criterion 9: the deadline from the context var is the one handed to the lanes."""
    seen = []

    async def audio(body, wav_path, settings, deadline):
        seen.append(deadline)
        return audio_result()

    async def video(body, webm_path, settings, deadline):
        seen.append(deadline)
        return video_result()

    monkeypatch.setattr(pipeline, "run_audio_lane", audio)
    monkeypatch.setattr(pipeline, "run_video_lane", video)
    mine = Deadline(50.0)
    token = deadline_var.set(mine)
    try:
        await run(tmp_path)
    finally:
        deadline_var.reset(token)
    assert seen == [mine, mine]


# 14. deadline over HTTP --------------------------------------------------------------------


@pytest.fixture
def deadline_settings(tmp_path):
    return worker_settings(tmp_path, request_deadline_seconds=0.3, in_progress_ttl_seconds=130)


@pytest.fixture
def pipeline_app(deadline_settings, redis):
    return create_app(deadline_settings, redis=redis, processor=pipeline_processor)


@pytest.fixture
async def pipeline_client(pipeline_app):
    transport = httpx.ASGITransport(app=pipeline_app, raise_app_exceptions=False)
    async with pipeline_app.router.lifespan_context(pipeline_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def test_slow_lanes_past_deadline_give_504_cancel_both_and_release(
    pipeline_client, auth, redis, tmp_path, lanes
):
    """criterion 14"""
    lanes.audio_behavior = "hang"
    lanes.video_behavior = "hang"

    response = await pipeline_client.post("/v1/analyses", json=valid_body(), headers=auth)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "TIMEOUT"
    assert lanes.audio_cancelled
    assert lanes.video_cancelled
    assert await redis.exists(KEY) == 0
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()

    lanes.audio_behavior = "ok"
    lanes.video_behavior = "ok"
    retry = await pipeline_client.post("/v1/analyses", json=valid_body(), headers=auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers


async def test_one_slow_lane_past_deadline_still_gives_504_even_with_flag(
    pipeline_client, deadline_settings, auth, lanes
):
    """criterion 14: deadline expiry is always 504, even with the audio-only flag."""
    deadline_settings.allow_audio_only_on_video_failure = True
    lanes.video_behavior = "hang"
    lanes.audio_behavior = "ok"

    response = await pipeline_client.post("/v1/analyses", json=valid_body(), headers=auth)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "TIMEOUT"
    assert lanes.video_cancelled


@pytest.fixture(autouse=True)
def _stub_upload_clips(monkeypatch):
    async def no_upload(*args, **kwargs):
        return None

    monkeypatch.setattr("app.pipeline.upload_clips", no_upload)
