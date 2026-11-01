"""POST /v1/analyses with clip upload: pipeline wiring and end to end through moto."""

import logging
from pathlib import Path

import boto3
import httpx
import pytest
import respx
from audio_helpers import BUCKET, REGION, expected_range, read_wav_bytes, use_fake_aws, write_wav
from conftest import valid_body
from lane_helpers import (
    REQUEST_ID,
    audio_payload,
    audio_result,
    lane_miscue,
    video_payload,
    video_result,
    worker_settings,
)
from moto import mock_aws

from app import pipeline
from app.deadline import Deadline
from app.errors import MAIError
from app.main import create_app
from app.media import ProbeResult
from app.models import AnalysisRequest, AnalysisResponse, ErrorCode
from app.pipeline import pipeline_processor

pytestmark = pytest.mark.anyio

KEY = f"mai:req:{REQUEST_ID}"
AUDIO_URL = "http://audio.test:8001/v1/jobs"
VIDEO_URL = "http://video.test:8002/v1/jobs"
RECORDING_MS = 3000


@pytest.fixture
def settings(tmp_path):
    return worker_settings(
        tmp_path, s3_bucket=BUCKET, request_deadline_seconds=30, in_progress_ttl_seconds=130
    )


@pytest.fixture
def aws(monkeypatch):
    use_fake_aws(monkeypatch)
    with mock_aws():
        yield boto3.client("s3", region_name=REGION)


def create_bucket(aws) -> None:
    aws.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})


@pytest.fixture
def source_pcm():
    """Raw samples of the WAV that the fake convert step writes."""
    return {}


@pytest.fixture(autouse=True)
def fake_media(monkeypatch, source_pcm):
    async def probe(path, settings):
        return ProbeResult("matroska,webm", True, True, 1.0)

    async def convert(src, dest, settings):
        source_pcm["pcm"] = write_wav(dest, RECORDING_MS)

    monkeypatch.setattr(pipeline, "probe", probe)
    monkeypatch.setattr(pipeline, "convert_to_wav", convert)


# --- pipeline wiring (criterion 12) -------------------------------------------------------------


@pytest.fixture
def stubbed_lanes(monkeypatch):
    async def download(url, dest, *, max_bytes, deadline):
        dest.write_bytes(b"video")
        return 5

    async def audio(body, wav_path, settings, deadline):
        return audio_result()

    async def video(body, webm_path, settings, deadline):
        return video_result()

    monkeypatch.setattr(pipeline, "download_video", download)
    monkeypatch.setattr(pipeline, "run_audio_lane", audio)
    monkeypatch.setattr(pipeline, "run_video_lane", video)


async def test_pipeline_uploads_clips_from_scratch_wav_before_returning(
    settings, monkeypatch, stubbed_lanes, tmp_path
):
    # criterion 12
    seen = {}

    async def upload(response, wav, settings_, deadline):
        seen["args"] = (response, wav, settings_, deadline)
        seen["wav_exists"] = wav.exists()
        seen["scratch_exists"] = wav.parent.is_dir()

    monkeypatch.setattr("app.pipeline.upload_clips", upload)

    result = await pipeline_processor(AnalysisRequest(**valid_body()), settings)

    response, wav, settings_, deadline = seen["args"]
    assert response == result
    assert isinstance(response, AnalysisResponse) and len(response.miscues) == 1
    assert wav == Path(settings.scratch_dir) / REQUEST_ID / "audio.wav"
    assert settings_ is settings
    assert isinstance(deadline, Deadline)
    assert seen["wav_exists"] and seen["scratch_exists"]
    assert not (Path(settings.scratch_dir) / REQUEST_ID).exists()


async def test_pipeline_logs_upload_stage(settings, monkeypatch, stubbed_lanes, log_records):
    # criterion 12
    async def upload(*args):
        return None

    monkeypatch.setattr("app.pipeline.upload_clips", upload)

    await pipeline_processor(AnalysisRequest(**valid_body()), settings)

    [record] = [
        r
        for r in log_records.records
        if r.getMessage() == "stage finished" and getattr(r, "stage", None) == "upload"
    ]
    assert record.levelno == logging.INFO
    assert isinstance(record.duration_ms, int)
    assert record.clips == 1


async def test_pipeline_does_not_return_a_result_when_upload_fails(
    settings, monkeypatch, stubbed_lanes
):
    # criterion 12
    async def upload(*args):
        raise MAIError(ErrorCode.UPLOAD_FAILED, "boom")

    monkeypatch.setattr("app.pipeline.upload_clips", upload)

    with pytest.raises(MAIError) as raised:
        await pipeline_processor(AnalysisRequest(**valid_body()), settings)
    assert raised.value.code == ErrorCode.UPLOAD_FAILED
    assert not (Path(settings.scratch_dir) / REQUEST_ID).exists()


# --- end to end through HTTP with moto (criteria 13, 14) ---------------------------------------


@pytest.fixture
def app(settings, redis):
    return create_app(settings, redis=redis, processor=pipeline_processor)


@pytest.fixture
def routes():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://bucket\.s3\.ap-southeast-1\.amazonaws\.com/.*").mock(
            return_value=httpx.Response(200, content=b"video-bytes")
        )
        router.post(VIDEO_URL).mock(return_value=httpx.Response(200, json=video_payload()))
        audio = router.post(AUDIO_URL)
        audio.mock(
            return_value=httpx.Response(
                200,
                json=audio_payload(
                    miscues=[
                        lane_miscue(actual_phrase="end", start_ms=2800, end_ms=3000),
                        lane_miscue(actual_phrase="start", start_ms=0, end_ms=200),
                        lane_miscue(
                            actual_phrase="gap",
                            spoken_phrase="",
                            miscue_type="omission",
                            start_ms=1500,
                            end_ms=1500,
                        ),
                    ]
                ),
            )
        )
        yield router


async def post(client, auth):
    return await client.post("/v1/analyses", json=valid_body(), headers=auth)


async def test_every_miscue_clip_is_uploaded_with_expected_frames(
    async_client, auth, routes, aws, source_pcm, tmp_path
):
    # criterion 13
    create_bucket(aws)

    response = await post(async_client, auth)

    assert response.status_code == 200
    miscues = response.json()["miscues"]
    assert [(m["read_start"], m["read_end"]) for m in miscues] == [
        (0, 200),
        (1500, 1500),
        (2800, 3000),
    ]
    for miscue in miscues:
        obj = aws.get_object(Bucket=BUCKET, Key=miscue["audio_object_key"])
        assert obj["ContentType"] == "audio/wav"
        params, frames = read_wav_bytes(obj["Body"].read(), tmp_path)
        assert (params.framerate, params.nchannels, params.sampwidth) == (16000, 1, 2)
        first, last = expected_range(RECORDING_MS, miscue["read_start"], miscue["read_end"], 150)
        assert params.nframes == last - first
        assert frames == source_pcm["pcm"][first * 2 : last * 2]
    # start clamped (0..350 ms), omission 300 ms around 1500 ms, end clamped (2650..3000 ms)
    frame_counts = []
    for miscue in miscues:
        obj = aws.get_object(Bucket=BUCKET, Key=miscue["audio_object_key"])
        frame_counts.append(read_wav_bytes(obj["Body"].read(), tmp_path)[0].nframes)
    assert frame_counts == [350 * 16, 300 * 16, 350 * 16]
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_upload_failure_is_500_and_leaves_the_request_retryable(
    async_client, auth, routes, aws, redis, tmp_path
):
    # criterion 14: the bucket does not exist
    response = await post(async_client, auth)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "UPLOAD_FAILED"
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()
    assert await redis.exists(KEY) == 0  # lease released, nothing stored

    create_bucket(aws)
    retry = await post(async_client, auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers
    assert aws.list_objects_v2(Bucket=BUCKET)["KeyCount"] == 3
