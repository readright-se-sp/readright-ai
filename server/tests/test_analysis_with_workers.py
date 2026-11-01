"""POST /v1/analyses end to end with respx-mocked download and lane workers (criterion 17)."""

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml
from conftest import valid_body
from lane_helpers import REQUEST_ID, audio_payload, lane_miscue, video_payload, worker_settings
from test_contract import CONTRACT_PATH, validator

from app import pipeline
from app.main import create_app
from app.media import ProbeResult
from app.models import AnalysisResponse
from app.pipeline import pipeline_processor

pytestmark = pytest.mark.anyio

KEY = f"mai:req:{REQUEST_ID}"
AUDIO_URL = "http://audio.test:8001/v1/jobs"
VIDEO_URL = "http://video.test:8002/v1/jobs"
DEADLINE_SECONDS = 5.0


@pytest.fixture
def settings(tmp_path):
    return worker_settings(
        tmp_path, request_deadline_seconds=DEADLINE_SECONDS, in_progress_ttl_seconds=130
    )


@pytest.fixture
def app(settings, redis):
    return create_app(settings, redis=redis, processor=pipeline_processor)


@pytest.fixture(autouse=True)
def fake_media(monkeypatch):
    async def probe(path, settings):
        return ProbeResult("matroska,webm", True, True, 1.0)

    async def convert(src, dest, settings):
        dest.write_bytes(b"RIFF")

    monkeypatch.setattr(pipeline, "probe", probe)
    monkeypatch.setattr(pipeline, "convert_to_wav", convert)


class Routes:
    def __init__(self, router: respx.MockRouter):
        self.s3 = router.get(url__regex=r"https://bucket\.s3\.ap-southeast-1\.amazonaws\.com/.*")
        self.audio = router.post(AUDIO_URL)
        self.video = router.post(VIDEO_URL)
        self.healthy()

    def healthy(self) -> None:
        self.s3.mock(side_effect=None, return_value=httpx.Response(200, content=b"video-bytes"))
        self.audio.mock(side_effect=None, return_value=httpx.Response(200, json=audio_payload()))
        self.video.mock(side_effect=None, return_value=httpx.Response(200, json=video_payload()))


@pytest.fixture
def routes():
    with respx.mock(assert_all_called=False) as router:
        yield Routes(router)


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return yaml.safe_load(Path(CONTRACT_PATH).read_text(encoding="utf-8"))


async def post(client, auth):
    return await client.post("/v1/analyses", json=valid_body(), headers=auth)


def error_code(response) -> str:
    return response.json()["error"]["code"]


async def assert_clean_and_retryable(client, auth, redis, tmp_path, routes):
    """No scratch dir, no stuck lease, and a retry with the same request_id is processed."""
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()
    assert await redis.exists(KEY) == 0
    routes.healthy()
    retry = await post(client, auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_success_body_validates_against_model_and_be_contract(
    async_client, auth, routes, contract, tmp_path
):
    response = await post(async_client, auth)

    assert response.status_code == 200
    assert "X-MAI-Stub" not in response.headers
    parsed = AnalysisResponse.model_validate(response.json())
    validator(contract, "AnalysisResponse").validate(response.json())
    assert str(parsed.request_id) == REQUEST_ID
    assert parsed.reading_score == 91.5
    assert parsed.wpm == 72.0
    assert parsed.is_finger_pointing is True
    assert parsed.is_lack_expression is True
    [miscue] = parsed.miscues
    assert miscue.actual_phrase == "cat"
    assert (miscue.read_start, miscue.read_end) == (1000, 1400)
    assert miscue.audio_object_key == f"analyses/{REQUEST_ID}/miscues/miscue-0001.wav"
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_lane_requests_carry_job_id_scratch_paths_and_deadline(
    async_client, auth, routes, settings, tmp_path
):
    before_ms = int(time.time() * 1000)
    response = await post(async_client, auth)
    after_ms = int(time.time() * 1000)
    assert response.status_code == 200

    work = settings.scratch_dir / REQUEST_ID
    audio = json.loads(routes.audio.calls.last.request.content)
    video = json.loads(routes.video.calls.last.request.content)
    assert audio["job_id"] == video["job_id"] == REQUEST_ID
    assert Path(audio["wav_path"]).parent == work
    assert Path(video["webm_path"]).parent == work
    assert Path(audio["wav_path"]).name == "audio.wav"
    assert Path(video["webm_path"]).name == "input.webm"
    for sent in (audio, video):
        assert before_ms <= sent["deadline"] <= after_ms + int(DEADLINE_SECONDS * 1000)


async def test_replay_of_success_returns_stored_body(async_client, auth, routes):
    first = await post(async_client, auth)
    audio_calls = routes.audio.call_count
    second = await post(async_client, auth)

    assert second.status_code == 200
    assert second.headers["X-MAI-Replayed"] == "true"
    assert "X-MAI-Stub" not in second.headers
    assert second.json() == first.json()
    assert routes.audio.call_count == audio_calls


async def test_audio_no_speech_is_422(async_client, auth, routes, redis, tmp_path):
    routes.audio.mock(
        return_value=httpx.Response(
            422, json={"error": {"code": "NO_SPEECH", "message": "silence"}}
        )
    )
    response = await post(async_client, auth)
    assert response.status_code == 422
    assert error_code(response) == "NO_SPEECH_DETECTED"
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, routes)


async def test_audio_500_is_500_processing_failed(async_client, auth, routes, redis, tmp_path):
    routes.audio.mock(
        return_value=httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "x"}})
    )
    response = await post(async_client, auth)
    assert response.status_code == 500
    assert error_code(response) == "PROCESSING_FAILED"
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, routes)


async def test_audio_connection_refused_is_500(async_client, auth, routes, redis, tmp_path):
    routes.audio.mock(side_effect=httpx.ConnectError("connection refused"))
    response = await post(async_client, auth)
    assert response.status_code == 500
    assert error_code(response) == "PROCESSING_FAILED"
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, routes)


async def test_video_500_is_500_when_audio_only_flag_is_off(
    async_client, auth, routes, redis, tmp_path
):
    routes.video.mock(
        return_value=httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "x"}})
    )
    response = await post(async_client, auth)
    assert response.status_code == 500
    assert error_code(response) == "PROCESSING_FAILED"
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, routes)


async def test_video_500_gives_audio_only_result_when_flag_is_on(
    app, async_client, auth, routes, contract, tmp_path
):
    app.state.settings.allow_audio_only_on_video_failure = True
    routes.video.mock(
        return_value=httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "x"}})
    )
    response = await post(async_client, auth)

    assert response.status_code == 200
    body = response.json()
    AnalysisResponse.model_validate(body)
    validator(contract, "AnalysisResponse").validate(body)
    assert body["is_finger_pointing"] is False
    assert body["is_lack_expression"] is False
    assert body["reading_score"] == 91.5
    assert len(body["miscues"]) == 1
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_audio_error_with_audio_only_flag_still_fails(
    app, async_client, auth, routes, redis, tmp_path
):
    app.state.settings.allow_audio_only_on_video_failure = True
    routes.audio.mock(
        return_value=httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "x"}})
    )
    response = await post(async_client, auth)
    assert response.status_code == 500
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, routes)


async def test_miscues_are_sorted_in_the_http_response(async_client, auth, routes):
    routes.audio.mock(
        return_value=httpx.Response(
            200,
            json=audio_payload(
                miscues=[
                    lane_miscue(actual_phrase="late", start_ms=4000, end_ms=4200),
                    lane_miscue(actual_phrase="early", start_ms=100, end_ms=300),
                ]
            ),
        )
    )
    body = (await post(async_client, auth)).json()
    assert [m["actual_phrase"] for m in body["miscues"]] == ["early", "late"]
    assert [m["audio_object_key"].rsplit("/", 1)[1] for m in body["miscues"]] == [
        "miscue-0001.wav",
        "miscue-0002.wav",
    ]
