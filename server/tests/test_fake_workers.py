"""The fake lane workers, alone and wired into the server in-process (criteria 18, 19)."""

import time
from pathlib import Path

import httpx
import pytest
import respx
from conftest import valid_body
from lane_helpers import REQUEST_ID, worker_settings

from app import pipeline
from app.main import create_app
from app.media import ProbeResult
from app.models import AnalysisResponse
from app.pipeline import pipeline_processor
from app.workers import AudioLaneResult, VideoLaneResult
from devtools.fake_workers import audio_app, video_app

pytestmark = pytest.mark.anyio

PASSAGE = "Pam has a cat. It is on the bed."


def future_ms(seconds: float = 30.0) -> int:
    return int((time.time() + seconds) * 1000)


@pytest.fixture(autouse=True)
def _clean_behavior_env(monkeypatch):
    for name in ("FAKE_AUDIO_BEHAVIOR", "FAKE_VIDEO_BEHAVIOR", "FAKE_SLOW_SECONDS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
async def audio():
    transport = httpx.ASGITransport(app=audio_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://audio") as http:
        yield http


@pytest.fixture
async def video():
    transport = httpx.ASGITransport(app=video_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://video") as http:
        yield http


@pytest.fixture
def wav(tmp_path) -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFF")
    return path


@pytest.fixture
def webm(tmp_path) -> Path:
    path = tmp_path / "input.webm"
    path.write_bytes(b"webm")
    return path


def audio_job(wav: Path, **overrides):
    job = {
        "job_id": REQUEST_ID,
        "deadline": future_ms(),
        "wav_path": str(wav),
        "passage_title": "Pam's Cat",
        "passage_content": PASSAGE,
    }
    job.update(overrides)
    return job


def video_job(webm: Path, **overrides):
    job = {"job_id": REQUEST_ID, "deadline": future_ms(), "webm_path": str(webm)}
    job.update(overrides)
    return job


def error_of(response) -> dict:
    return response.json()["error"]


# 18. endpoints -------------------------------------------------------------------------------


async def check_health_and_stats(client):
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    stats = await client.get("/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert isinstance(body["queue_length"], int)
    assert body["avg_processing_seconds"] is None or isinstance(
        body["avg_processing_seconds"], int | float
    )


async def test_audio_health_ready_and_stats(audio):
    """criterion 18"""
    await check_health_and_stats(audio)


async def test_video_health_ready_and_stats(video):
    """criterion 18"""
    await check_health_and_stats(video)


# 18. audio -----------------------------------------------------------------------------------


async def test_audio_job_returns_a_valid_result_derived_from_the_passage(audio, wav):
    """criterion 18"""
    response = await audio.post("/v1/jobs", json=audio_job(wav))
    assert response.status_code == 200
    result = AudioLaneResult.model_validate(response.json())
    assert response.json()["job_id"] == REQUEST_ID

    first, third = result.miscues[0], result.miscues[1]
    assert first.miscue_type == "substitution"
    assert first.actual_phrase == "Pam"
    assert first.spoken_phrase != first.actual_phrase
    assert third.miscue_type == "omission"
    assert third.actual_phrase == "a"
    assert third.spoken_phrase == ""


async def test_audio_result_is_deterministic(audio, wav):
    """criterion 18"""
    one = await audio.post("/v1/jobs", json=audio_job(wav))
    two = await audio.post("/v1/jobs", json=audio_job(wav))
    assert one.json() == two.json()


async def test_audio_result_changes_with_the_passage(audio, wav):
    """criterion 18"""
    other = await audio.post("/v1/jobs", json=audio_job(wav, passage_content="Sam has a red hat."))
    miscues = AudioLaneResult.model_validate(other.json()).miscues
    assert miscues[0].actual_phrase == "Sam"


async def test_audio_missing_file_is_422_media_unreadable(audio, tmp_path):
    """criterion 18"""
    response = await audio.post("/v1/jobs", json=audio_job(tmp_path / "missing.wav"))
    assert response.status_code == 422
    assert error_of(response)["code"] == "MEDIA_UNREADABLE"
    assert isinstance(error_of(response)["message"], str)


async def test_audio_past_deadline_is_504_deadline_exceeded(audio, wav):
    """criterion 18"""
    response = await audio.post("/v1/jobs", json=audio_job(wav, deadline=future_ms(-5)))
    assert response.status_code == 504
    assert error_of(response)["code"] == "DEADLINE_EXCEEDED"


async def test_audio_invalid_job_is_400_invalid_job(audio, wav):
    """criterion 18"""
    job = audio_job(wav)
    del job["wav_path"]
    response = await audio.post("/v1/jobs", json=job)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_JOB"


async def test_audio_no_speech_behavior(audio, wav, monkeypatch):
    """criterion 18"""
    monkeypatch.setenv("FAKE_AUDIO_BEHAVIOR", "no_speech")
    response = await audio.post("/v1/jobs", json=audio_job(wav))
    assert response.status_code == 422
    assert error_of(response)["code"] == "NO_SPEECH"


async def test_audio_error_behavior(audio, wav, monkeypatch):
    """criterion 18"""
    monkeypatch.setenv("FAKE_AUDIO_BEHAVIOR", "error")
    response = await audio.post("/v1/jobs", json=audio_job(wav))
    assert response.status_code == 500
    assert error_of(response)["code"] == "INTERNAL"


async def test_audio_slow_behavior_waits_for_the_configured_seconds(audio, wav, monkeypatch):
    """criterion 18"""
    monkeypatch.setenv("FAKE_AUDIO_BEHAVIOR", "slow")
    monkeypatch.setenv("FAKE_SLOW_SECONDS", "0.3")
    started = time.monotonic()
    response = await audio.post("/v1/jobs", json=audio_job(wav))
    elapsed = time.monotonic() - started
    assert elapsed >= 0.25
    assert response.status_code == 200


async def test_audio_behavior_is_read_on_every_request(audio, wav, monkeypatch):
    """criterion 18"""
    assert (await audio.post("/v1/jobs", json=audio_job(wav))).status_code == 200
    monkeypatch.setenv("FAKE_AUDIO_BEHAVIOR", "error")
    assert (await audio.post("/v1/jobs", json=audio_job(wav))).status_code == 500
    monkeypatch.setenv("FAKE_AUDIO_BEHAVIOR", "ok")
    assert (await audio.post("/v1/jobs", json=audio_job(wav))).status_code == 200


# 18. video -----------------------------------------------------------------------------------


async def test_video_job_returns_false_flags(video, webm):
    """criterion 18"""
    response = await video.post("/v1/jobs", json=video_job(webm))
    assert response.status_code == 200
    result = VideoLaneResult.model_validate(response.json())
    assert response.json()["job_id"] == REQUEST_ID
    assert result.is_finger_pointing is False
    assert result.is_lack_expression is False


async def test_video_missing_file_is_422_media_unreadable(video, tmp_path):
    """criterion 18"""
    response = await video.post("/v1/jobs", json=video_job(tmp_path / "missing.webm"))
    assert response.status_code == 422
    assert error_of(response)["code"] == "MEDIA_UNREADABLE"


async def test_video_past_deadline_is_504_deadline_exceeded(video, webm):
    """criterion 18"""
    response = await video.post("/v1/jobs", json=video_job(webm, deadline=future_ms(-5)))
    assert response.status_code == 504
    assert error_of(response)["code"] == "DEADLINE_EXCEEDED"


async def test_video_invalid_job_is_400_invalid_job(video, webm):
    """criterion 18"""
    job = video_job(webm)
    del job["webm_path"]
    response = await video.post("/v1/jobs", json=job)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_JOB"


async def test_video_error_behavior(video, webm, monkeypatch):
    """criterion 18"""
    monkeypatch.setenv("FAKE_VIDEO_BEHAVIOR", "error")
    response = await video.post("/v1/jobs", json=video_job(webm))
    assert response.status_code == 500
    assert error_of(response)["code"] == "INTERNAL"


async def test_video_slow_behavior_waits_for_the_configured_seconds(video, webm, monkeypatch):
    """criterion 18"""
    monkeypatch.setenv("FAKE_VIDEO_BEHAVIOR", "slow")
    monkeypatch.setenv("FAKE_SLOW_SECONDS", "0.3")
    started = time.monotonic()
    response = await video.post("/v1/jobs", json=video_job(webm))
    assert time.monotonic() - started >= 0.25
    assert response.status_code == 200


# 19. server + fake workers in-process --------------------------------------------------------


def forward_to(asgi_app, base_url: str):
    async def forward(request: httpx.Request) -> httpx.Response:
        transport = httpx.ASGITransport(app=asgi_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as http:
            reply = await http.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={"content-type": request.headers.get("content-type", "application/json")},
            )
        return httpx.Response(
            reply.status_code,
            content=reply.content,
            headers={"content-type": reply.headers.get("content-type", "application/json")},
        )

    return forward


@pytest.fixture
def settings(tmp_path):
    return worker_settings(tmp_path, request_deadline_seconds=5.0, in_progress_ttl_seconds=130)


@pytest.fixture
def app(settings, redis):
    return create_app(settings, redis=redis, processor=pipeline_processor)


@pytest.fixture
def wired(monkeypatch):
    async def probe(path, settings):
        return ProbeResult("matroska,webm", True, True, 1.0)

    async def convert(src, dest, settings):
        dest.write_bytes(b"RIFF")

    monkeypatch.setattr(pipeline, "probe", probe)
    monkeypatch.setattr(pipeline, "convert_to_wav", convert)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://bucket\.s3\.ap-southeast-1\.amazonaws\.com/.*").respond(
            200, content=b"video-bytes"
        )
        router.post("http://audio.test:8001/v1/jobs").mock(
            side_effect=forward_to(audio_app, "http://audio.test:8001")
        )
        router.post("http://video.test:8002/v1/jobs").mock(
            side_effect=forward_to(video_app, "http://video.test:8002")
        )
        yield router


async def test_server_with_fake_workers_returns_passage_derived_miscues(
    async_client, auth, wired, tmp_path
):
    """criterion 19"""
    response = await async_client.post("/v1/analyses", json=valid_body(), headers=auth)

    assert response.status_code == 200, response.text
    parsed = AnalysisResponse.model_validate(response.json())
    assert parsed.is_finger_pointing is False
    assert parsed.is_lack_expression is False
    assert parsed.miscues, "fake audio worker should report miscues"
    assert parsed.miscues[0].miscue_type == "substitution"
    assert parsed.miscues[0].actual_phrase == "Pam"
    assert parsed.miscues[1].miscue_type == "omission"
    assert parsed.miscues[1].actual_phrase == "a"
    prefix = f"analyses/{REQUEST_ID}/miscues/"
    assert [m.audio_object_key for m in parsed.miscues] == [
        f"{prefix}miscue-{n:04d}.wav" for n in range(1, len(parsed.miscues) + 1)
    ]
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_server_with_fake_workers_uses_custom_key_prefix(async_client, auth, wired):
    """criterion 19"""
    body = valid_body(output_key_prefix="custom/path")
    response = await async_client.post("/v1/analyses", json=body, headers=auth)

    assert response.status_code == 200, response.text
    keys = [m["audio_object_key"] for m in response.json()["miscues"]]
    assert keys[0] == "custom/path/miscue-0001.wav"
    assert all(key.startswith("custom/path/miscue-") for key in keys)


async def test_server_maps_fake_worker_no_speech_to_422(async_client, auth, wired, monkeypatch):
    """criterion 19"""
    monkeypatch.setenv("FAKE_AUDIO_BEHAVIOR", "no_speech")
    response = await async_client.post("/v1/analyses", json=valid_body(), headers=auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_SPEECH_DETECTED"
