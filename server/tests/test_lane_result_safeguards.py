"""Safeguards on worker URLs, lane result values, merge failures and job ids (criteria 22-26)."""

import logging
from pathlib import Path

import httpx
import pytest
import respx
from conftest import API_KEY, valid_body
from lane_helpers import (
    REQUEST_ID,
    audio_payload,
    audio_result,
    video_payload,
    video_result,
    worker_settings,
)
from pydantic import ValidationError

from app import pipeline
from app.config import Settings
from app.deadline import Deadline
from app.errors import MAIError
from app.media import ProbeResult
from app.models import AnalysisRequest, ErrorCode, Miscue
from app.pipeline import pipeline_processor
from app.workers import AudioLaneResult, run_audio_lane, run_video_lane

pytestmark = pytest.mark.anyio

AUDIO_URL = "http://audio.test:8001/v1/jobs"
VIDEO_URL = "http://video.test:8002/v1/jobs"
JSON = {"content-type": "application/json"}
OTHER_JOB = "11111111-1111-4111-8111-111111111111"


def body() -> AnalysisRequest:
    return AnalysisRequest(**valid_body())


@pytest.fixture
def workers():
    with respx.mock(assert_all_called=False) as router:
        yield router


async def call_audio(settings=None):
    settings = settings or worker_settings(Path("/x"))
    return await run_audio_lane(body(), Path("/s/audio.wav"), settings, Deadline(10.0))


async def call_video(settings=None):
    settings = settings or worker_settings(Path("/x"))
    return await run_video_lane(body(), Path("/s/input.webm"), settings, Deadline(10.0))


@pytest.fixture
def stages(monkeypatch):
    async def download(url, dest, *, max_bytes, deadline):
        dest.write_bytes(b"video")
        return 5

    async def probe(path, settings):
        return ProbeResult("matroska,webm", True, True, 1.0)

    async def convert(src, dest, settings):
        dest.write_bytes(b"RIFF")

    monkeypatch.setattr(pipeline, "download_video", download)
    monkeypatch.setattr(pipeline, "probe", probe)
    monkeypatch.setattr(pipeline, "convert_to_wav", convert)


# 22. worker URL validation ---------------------------------------------------------------------


@pytest.mark.parametrize("field", ["audio_worker_url", "video_worker_url"])
@pytest.mark.parametrize(
    "value", ["not a url", "ftp://worker:21", "/relative/path", "http://", "worker:8001", ""]
)
def test_invalid_worker_url_is_rejected(field, value):
    """criterion 22"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key=API_KEY, **{field: value})  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "value", ["http://audio:8001", "https://worker.example.com", "http://127.0.0.1:9000/"]
)
def test_valid_worker_urls_are_accepted(value):
    """criterion 22"""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, api_key=API_KEY, audio_worker_url=value, video_worker_url=value
    )
    assert s.audio_worker_url == value


def test_invalid_worker_url_from_environment_is_rejected(monkeypatch):
    """criterion 22"""
    monkeypatch.setenv("MAI_API_KEY", API_KEY)
    monkeypatch.setenv("MAI_VIDEO_WORKER_URL", "nonsense")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_invalid_url_reaching_a_lane_maps_to_processing_failed(lane):
    """criterion 22: defensive mapping when validation was bypassed."""
    settings = worker_settings(Path("/x")).model_copy(update={f"{lane}_worker_url": "http://[bad"})
    call = call_audio if lane == "audio" else call_video
    with pytest.raises(MAIError) as exc:
        await call(settings)
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.details["lane"] == lane


async def test_bad_video_url_with_audio_only_flag_returns_audio_result(
    tmp_path, monkeypatch, stages
):
    """criterion 22"""

    async def audio(body, wav_path, settings, deadline):
        return audio_result()

    monkeypatch.setattr(pipeline, "run_audio_lane", audio)
    settings = worker_settings(tmp_path, allow_audio_only_on_video_failure=True).model_copy(
        update={"video_worker_url": "http://[bad"}
    )
    result = await pipeline_processor(body(), settings)
    assert result.is_finger_pointing is False
    assert result.is_lack_expression is False
    assert result.reading_score == audio_result().reading_score


# 23. finite, non-negative floats ----------------------------------------------------------------


@pytest.mark.parametrize("field", ["reading_score", "wpm"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_audio_numbers_are_rejected(field, value):
    """criterion 23"""
    with pytest.raises(ValidationError):
        AudioLaneResult.model_validate(audio_payload(**{field: value}))


def test_negative_wpm_is_rejected():
    """criterion 23"""
    with pytest.raises(ValidationError):
        AudioLaneResult.model_validate(audio_payload(wpm=-0.5))


@pytest.mark.parametrize("reading_score", [-0.1, 100.1])
def test_reading_score_outside_0_to_100_is_rejected(reading_score):
    """criterion 23"""
    with pytest.raises(ValidationError):
        AudioLaneResult.model_validate(audio_payload(reading_score=reading_score))


@pytest.mark.parametrize("reading_score", [0.0, 100.0])
def test_zero_wpm_and_reading_score_bounds_are_accepted(reading_score):
    """criterion 23: wpm >= 0; reading_score in [0, 100] inclusive."""
    result = AudioLaneResult.model_validate(audio_payload(wpm=0.0, reading_score=reading_score))
    assert result.wpm == 0.0
    assert result.reading_score == reading_score


@pytest.mark.parametrize(
    ("reading_score", "wpm"),
    [
        ("NaN", "70.0"),
        ("90.0", "Infinity"),
        ("-Infinity", "70.0"),
        ("90.0", "-3.0"),
        ("100.5", "70.0"),
    ],
)
async def test_non_finite_or_negative_numbers_from_worker_map_to_processing_failed(
    workers, reading_score, wpm
):
    """criterion 23: NaN and Infinity are sent as raw JSON text."""
    text = (
        f'{{"job_id": "{REQUEST_ID}", "reading_score": {reading_score}, "wpm": {wpm}, '
        '"is_word_by_word": false, "is_disregard_punctuation": true, '
        '"is_monotonous": false, "is_hardly_audible": false, "miscues": []}'
    )
    workers.post(AUDIO_URL).mock(
        return_value=httpx.Response(200, content=text.encode(), headers=JSON)
    )
    with pytest.raises(MAIError) as exc:
        await call_audio()
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.details == {"lane": "audio"}


# 24. merge failure ------------------------------------------------------------------------------


async def test_merge_validation_error_maps_to_processing_failed(tmp_path, monkeypatch, stages):
    """criterion 24"""

    async def audio(body, wav_path, settings, deadline):
        return audio_result()

    async def video(body, webm_path, settings, deadline):
        return video_result()

    def broken_merge(*args, **kwargs):
        Miscue.model_validate({})  # raises a pydantic ValidationError

    monkeypatch.setattr(pipeline, "run_audio_lane", audio)
    monkeypatch.setattr(pipeline, "run_video_lane", video)
    monkeypatch.setattr(pipeline, "merge_results", broken_merge)
    with pytest.raises(MAIError) as exc:
        await pipeline_processor(body(), worker_settings(tmp_path))
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.status == 500
    assert exc.value.details == {"lane": "audio"}
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


# 25. code and message parsed independently ------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        {"code": "NO_SPEECH"},
        {"code": "NO_SPEECH", "message": None},
        {"code": "NO_SPEECH", "message": 42},
        {"code": "NO_SPEECH", "message": {"nested": True}},
    ],
)
async def test_no_speech_without_a_string_message_still_maps(workers, error):
    """criterion 25"""
    workers.post(AUDIO_URL).respond(422, json={"error": error})
    with pytest.raises(MAIError) as exc:
        await call_audio()
    assert exc.value.code == ErrorCode.NO_SPEECH_DETECTED


@pytest.mark.parametrize("lane", ["audio", "video"])
@pytest.mark.parametrize(
    "error", [{"code": "DEADLINE_EXCEEDED"}, {"code": "DEADLINE_EXCEEDED", "message": 7}]
)
async def test_deadline_exceeded_without_a_string_message_still_maps(workers, lane, error):
    """criterion 25"""
    url, call = (AUDIO_URL, call_audio) if lane == "audio" else (VIDEO_URL, call_video)
    workers.post(url).respond(504, json={"error": error})
    with pytest.raises(MAIError) as exc:
        await call()
    assert exc.value.code == ErrorCode.TIMEOUT


# 26. job_id echo --------------------------------------------------------------------------------


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_result_for_another_job_is_rejected_and_warned(workers, log_records, lane):
    """criterion 26"""
    if lane == "audio":
        workers.post(AUDIO_URL).respond(200, json=audio_payload(job_id=OTHER_JOB))
        call = call_audio
    else:
        workers.post(VIDEO_URL).respond(200, json=video_payload(job_id=OTHER_JOB))
        call = call_video
    with pytest.raises(MAIError) as exc:
        await call()
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.details["lane"] == lane
    warnings = [
        r
        for r in log_records.records
        if r.levelno == logging.WARNING
        and r.getMessage() == "lane returned a result for another job"
    ]
    assert warnings


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_job_id_comparison_ignores_case(workers, lane):
    """criterion 26"""
    upper = REQUEST_ID.upper()
    if lane == "audio":
        workers.post(AUDIO_URL).respond(200, json=audio_payload(job_id=upper))
        result = await call_audio()
    else:
        workers.post(VIDEO_URL).respond(200, json=video_payload(job_id=upper))
        result = await call_video()
    assert str(result.job_id).lower() == REQUEST_ID


@pytest.fixture(autouse=True)
def _stub_upload_clips(monkeypatch):
    async def no_upload(*args, **kwargs):
        return None

    monkeypatch.setattr("app.pipeline.upload_clips", no_upload)
