"""Lane models, request shape, timeouts and error mapping (criteria 4-8)."""

import json
import time
from pathlib import Path

import httpx
import pytest
import respx
from conftest import valid_body
from lane_helpers import (
    REQUEST_ID,
    audio_payload,
    lane_miscue,
    video_payload,
    worker_settings,
)
from pydantic import ValidationError

from app.deadline import Deadline
from app.errors import MAIError
from app.models import AnalysisRequest, ErrorCode, MiscueType
from app.workers import (
    AudioLaneResult,
    LaneMiscue,
    VideoLaneResult,
    deadline_epoch_ms,
    run_audio_lane,
    run_video_lane,
)

pytestmark = pytest.mark.anyio

AUDIO_URL = "http://audio.test:8001/v1/jobs"
VIDEO_URL = "http://video.test:8002/v1/jobs"
WAV = Path("/scratch/req/audio.wav")
WEBM = Path("/scratch/req/input.webm")


def body() -> AnalysisRequest:
    return AnalysisRequest(**valid_body())


def worker_error(code: str, message: str = "worker says no") -> dict:
    return {"error": {"code": code, "message": message}}


@pytest.fixture
def workers():
    with respx.mock(assert_all_called=False) as router:
        yield router


async def call_audio(settings, deadline=None):
    return await run_audio_lane(body(), WAV, settings, deadline or Deadline(10.0))


async def call_video(settings, deadline=None):
    return await run_video_lane(body(), WEBM, settings, deadline or Deadline(10.0))


# 4. models -------------------------------------------------------------------------------------


def test_lane_miscue_parses_and_ignores_unknown_fields():
    """criterion 4"""
    miscue = LaneMiscue.model_validate(lane_miscue(surprise="ignored"))
    assert miscue.start_ms == 1000
    assert miscue.end_ms == 1400
    assert miscue.miscue_type is MiscueType.SUBSTITUTION
    assert not hasattr(miscue, "surprise")


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_ms": -1},
        {"start_ms": 500, "end_ms": 499},
        {"miscue_type": "stuttering"},
        {"start_ms": 1.5},
    ],
)
def test_lane_miscue_rejects_invalid_values(overrides):
    """criterion 4"""
    with pytest.raises(ValidationError):
        LaneMiscue.model_validate(lane_miscue(**overrides))


def test_lane_miscue_allows_zero_length_and_zero_start():
    """criterion 4"""
    miscue = LaneMiscue.model_validate(lane_miscue(start_ms=0, end_ms=0))
    assert (miscue.start_ms, miscue.end_ms) == (0, 0)


def test_audio_result_parses_and_ignores_unknown_fields():
    """criterion 4"""
    result = AudioLaneResult.model_validate(audio_payload(extra_check=True))
    assert result.reading_score == 91.5
    assert result.wpm == 72.0
    assert len(result.miscues) == 1
    assert isinstance(result.miscues[0], LaneMiscue)


def test_audio_result_requires_scores():
    """criterion 4"""
    payload = audio_payload()
    del payload["reading_score"]
    with pytest.raises(ValidationError):
        AudioLaneResult.model_validate(payload)


def test_video_result_parses_and_ignores_unknown_fields():
    """criterion 4"""
    result = VideoLaneResult.model_validate(video_payload(is_something_new=True))
    assert result.is_finger_pointing is True
    assert result.is_lack_expression is True


def test_video_result_requires_its_flags():
    """criterion 4"""
    with pytest.raises(ValidationError):
        VideoLaneResult.model_validate({"job_id": REQUEST_ID, "is_finger_pointing": True})


# 5. deadline_epoch_ms ---------------------------------------------------------------------------


def test_deadline_epoch_ms_is_wall_clock_plus_remaining(monkeypatch):
    """criterion 5"""
    monkeypatch.setattr(time, "time", lambda: 1_000.0)
    deadline = Deadline(10.0, clock=lambda: 5.0)
    assert deadline_epoch_ms(deadline) == 1_010_000


def test_deadline_epoch_ms_is_an_int_near_now_plus_remaining():
    """criterion 5"""
    before = int(time.time() * 1000)
    value = deadline_epoch_ms(Deadline(30.0))
    after = int(time.time() * 1000)
    assert isinstance(value, int)
    assert before + 29_000 <= value <= after + 30_000


# 6/7. request shape -----------------------------------------------------------------------------


async def test_audio_lane_posts_job_and_returns_result(workers):
    """criterion 6"""
    route = workers.post(AUDIO_URL).respond(200, json=audio_payload())
    before = int(time.time() * 1000)
    result = await call_audio(worker_settings(Path("/x")), Deadline(20.0))
    after = int(time.time() * 1000)

    assert isinstance(result, AudioLaneResult)
    assert result.reading_score == 91.5
    sent = route.calls.last.request
    payload = json.loads(sent.content)
    assert set(payload) == {"job_id", "deadline", "wav_path", "passage_title", "passage_content"}
    assert payload["job_id"] == REQUEST_ID
    assert payload["wav_path"] == str(WAV)
    assert payload["passage_title"] == valid_body()["passage_title"]
    assert payload["passage_content"] == valid_body()["passage_content"]
    assert isinstance(payload["deadline"], int)
    assert before + 19_000 <= payload["deadline"] <= after + 20_000


async def test_audio_worker_url_trailing_slash_is_dropped(workers):
    """criterion 6"""
    route = workers.post(AUDIO_URL).respond(200, json=audio_payload())
    settings = worker_settings(Path("/x"), audio_worker_url="http://audio.test:8001/")
    await call_audio(settings)
    assert route.called


async def test_video_lane_posts_job_and_returns_result(workers):
    """criterion 7"""
    route = workers.post(VIDEO_URL).respond(200, json=video_payload())
    result = await call_video(worker_settings(Path("/x")), Deadline(20.0))

    assert isinstance(result, VideoLaneResult)
    assert result.is_finger_pointing is True
    payload = json.loads(route.calls.last.request.content)
    assert set(payload) == {"job_id", "deadline", "webm_path"}
    assert payload["job_id"] == REQUEST_ID
    assert payload["webm_path"] == str(WEBM)
    assert isinstance(payload["deadline"], int)


async def test_video_worker_url_trailing_slash_is_dropped(workers):
    """criterion 7"""
    route = workers.post(VIDEO_URL).respond(200, json=video_payload())
    settings = worker_settings(Path("/x"), video_worker_url="http://video.test:8002/")
    await call_video(settings)
    assert route.called


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_lane_timeouts_follow_connect_setting_and_remaining_deadline(workers, lane):
    """criteria 6, 7"""
    url, payload, call = (
        (AUDIO_URL, audio_payload(), call_audio)
        if lane == "audio"
        else (VIDEO_URL, video_payload(), call_video)
    )
    route = workers.post(url).respond(200, json=payload)
    settings = worker_settings(Path("/x"), worker_connect_timeout_seconds=2.0)
    await call(settings, Deadline(10.0))
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["connect"] == pytest.approx(2.0)
    for kind in ("read", "write", "pool"):
        assert 9.0 < timeout[kind] <= 10.0


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_connect_timeout_never_exceeds_remaining_deadline(workers, lane):
    """criteria 6, 7"""
    url, payload, call = (
        (AUDIO_URL, audio_payload(), call_audio)
        if lane == "audio"
        else (VIDEO_URL, video_payload(), call_video)
    )
    route = workers.post(url).respond(200, json=payload)
    settings = worker_settings(Path("/x"), worker_connect_timeout_seconds=5.0)
    await call(settings, Deadline(1.0))
    timeout = route.calls.last.request.extensions["timeout"]
    assert 0.0 < timeout["connect"] <= 1.0
    assert timeout["read"] <= 1.0


# 8. error mapping -------------------------------------------------------------------------------

LANES = {
    "audio": (AUDIO_URL, audio_payload, call_audio),
    "video": (VIDEO_URL, video_payload, call_video),
}


async def test_audio_no_speech_maps_to_no_speech_detected(workers):
    """criterion 8"""
    workers.post(AUDIO_URL).respond(422, json=worker_error("NO_SPEECH"))
    with pytest.raises(MAIError) as exc:
        await call_audio(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.NO_SPEECH_DETECTED
    assert exc.value.status == 422


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_lane_deadline_exceeded_maps_to_timeout(workers, lane):
    """criterion 8"""
    url, _, call = LANES[lane]
    workers.post(url).respond(504, json=worker_error("DEADLINE_EXCEEDED"))
    with pytest.raises(MAIError) as exc:
        await call(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.TIMEOUT
    assert exc.value.status == 504


@pytest.mark.parametrize("lane", ["audio", "video"])
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (500, "INTERNAL"),
        (400, "INVALID_JOB"),
        (422, "MEDIA_UNREADABLE"),
        (503, "SOMETHING_NEW"),
    ],
)
async def test_other_worker_errors_map_to_processing_failed(workers, lane, status, code):
    """criterion 8"""
    url, _, call = LANES[lane]
    workers.post(url).respond(status, json=worker_error(code))
    with pytest.raises(MAIError) as exc:
        await call(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.status == 500
    assert exc.value.details["lane"] == lane


async def test_video_no_speech_is_just_a_processing_failure(workers):
    """criterion 8: NO_SPEECH has special meaning for the audio lane only."""
    workers.post(VIDEO_URL).respond(422, json=worker_error("NO_SPEECH"))
    with pytest.raises(MAIError) as exc:
        await call_video(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.PROCESSING_FAILED


@pytest.mark.parametrize("lane", ["audio", "video"])
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, content=b"<html>bad gateway</html>"),
        httpx.Response(500, content=b""),
        httpx.Response(500, json={"unexpected": "shape"}),
        httpx.Response(200, content=b"not json at all"),
        httpx.Response(200, json=[1, 2, 3]),
        httpx.Response(200, json={"job_id": REQUEST_ID}),
    ],
)
async def test_non_json_or_invalid_bodies_map_to_processing_failed(workers, lane, response):
    """criterion 8"""
    url, _, call = LANES[lane]
    workers.post(url).mock(return_value=response)
    with pytest.raises(MAIError) as exc:
        await call(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.details["lane"] == lane


async def test_audio_result_failing_validation_maps_to_processing_failed(workers):
    """criterion 8: end before start inside a miscue."""
    bad = audio_payload(miscues=[lane_miscue(start_ms=900, end_ms=100)])
    workers.post(AUDIO_URL).respond(200, json=bad)
    with pytest.raises(MAIError) as exc:
        await call_audio(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.details["lane"] == "audio"


@pytest.mark.parametrize("lane", ["audio", "video"])
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_http_errors_map_to_processing_failed(workers, lane, error):
    """criterion 8"""
    url, _, call = LANES[lane]
    workers.post(url).mock(side_effect=error)
    with pytest.raises(MAIError) as exc:
        await call(worker_settings(Path("/x")))
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.details["lane"] == lane


@pytest.mark.parametrize("lane", ["audio", "video"])
async def test_http_error_after_deadline_expired_maps_to_timeout(workers, lane):
    """criterion 8"""
    url, _, call = LANES[lane]
    now = [0.0]

    def clock() -> float:
        return now[0]

    deadline = Deadline(5.0, clock=clock)

    def expire_then_fail(request):
        now[0] = 10.0
        raise httpx.ReadTimeout("read timed out")

    workers.post(url).mock(side_effect=expire_then_fail)
    with pytest.raises(MAIError) as exc:
        await call(worker_settings(Path("/x")), deadline)
    assert exc.value.code == ErrorCode.TIMEOUT


async def test_worker_error_message_is_logged_not_returned(workers, log_records):
    """criterion 8"""
    secret = "internal model exploded at /srv/models"
    workers.post(AUDIO_URL).respond(500, json=worker_error("INTERNAL", secret))
    with pytest.raises(MAIError) as exc:
        await call_audio(worker_settings(Path("/x")))

    assert secret not in exc.value.message
    assert secret not in str(exc.value.details)
    matching = [
        r
        for r in log_records.records
        if getattr(r, "lane", None) == "audio"
        and getattr(r, "worker_status", None) == 500
        and getattr(r, "worker_code", None) == "INTERNAL"
    ]
    assert matching, "expected a log record with lane, worker_status and worker_code"
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in matching)
    assert secret in logged
