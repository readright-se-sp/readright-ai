"""output_key_prefix is deprecated: accepted, logged, and never used to build keys."""

import logging

import httpx
import pytest
import yaml
from conftest import API_KEY, valid_body
from lane_helpers import REQUEST_ID, audio_result, video_result, worker_settings
from test_contract import CONTRACT_PATH

from app import pipeline
from app.main import create_app
from app.models import AnalysisRequest
from app.pipeline import pipeline_processor

pytestmark = pytest.mark.anyio

DEFAULT_PREFIX = f"analyses/{REQUEST_ID}/miscues/"
FOREIGN_PREFIX = "analyses/11111111-2222-3333-4444-555555555555/secret-area/"
MESSAGE = "output_key_prefix is deprecated and ignored"


@pytest.fixture
def stubbed_pipeline(monkeypatch):
    uploaded = []

    async def download(url, dest, *, max_bytes, deadline):
        dest.write_bytes(b"video")
        return 5

    async def probe(path, settings):
        return None

    async def convert(src, dest, settings):
        dest.write_bytes(b"RIFF")

    async def audio(body, wav, settings, deadline):
        return audio_result()

    async def video(body, webm, settings, deadline):
        return video_result()

    async def upload(response, wav, settings, deadline):
        uploaded.extend(m.audio_object_key for m in response.miscues)

    for name, fn in [
        ("download_video", download),
        ("probe", probe),
        ("convert_to_wav", convert),
        ("run_audio_lane", audio),
        ("run_video_lane", video),
        ("upload_clips", upload),
    ]:
        monkeypatch.setattr(pipeline, name, fn)
    return uploaded


async def test_keys_ignore_a_foreign_request_prefix(stubbed_pipeline, tmp_path):
    # criterion 23
    body = AnalysisRequest(**valid_body(output_key_prefix=FOREIGN_PREFIX))
    result = await pipeline_processor(body, worker_settings(tmp_path))
    keys = [m.audio_object_key for m in result.miscues]
    assert keys == [f"{DEFAULT_PREFIX}miscue-0001.wav"]
    assert stubbed_pipeline == keys


async def test_request_with_prefix_is_accepted_over_http(stubbed_pipeline, tmp_path, redis):
    # criterion 23: no 400, keys under the default prefix
    app = create_app(worker_settings(tmp_path), redis=redis, processor=pipeline_processor)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/v1/analyses",
                json=valid_body(output_key_prefix=FOREIGN_PREFIX),
                headers={"X-API-Key": API_KEY},
            )
    assert response.status_code == 200, response.text
    assert [m["audio_object_key"] for m in response.json()["miscues"]] == [
        f"{DEFAULT_PREFIX}miscue-0001.wav"
    ]


async def test_prefix_present_logs_deprecation_info_without_the_value(
    stubbed_pipeline, tmp_path, log_records
):
    # criterion 23
    body = AnalysisRequest(**valid_body(output_key_prefix=FOREIGN_PREFIX))
    await pipeline_processor(body, worker_settings(tmp_path))
    [record] = [r for r in log_records.records if r.getMessage() == MESSAGE]
    assert record.levelno == logging.INFO
    assert "secret-area" not in str(record.__dict__)


@pytest.mark.parametrize("given", [None, ""])
async def test_no_deprecation_log_without_a_prefix(stubbed_pipeline, tmp_path, log_records, given):
    # criterion 23
    body = AnalysisRequest(**valid_body(output_key_prefix=given))
    await pipeline_processor(body, worker_settings(tmp_path))
    assert not [r for r in log_records.records if r.getMessage() == MESSAGE]


def test_contract_marks_output_key_prefix_deprecated():
    # criterion 25
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    field = contract["components"]["schemas"]["AnalysisRequest"]["properties"]["output_key_prefix"]
    assert field["deprecated"] is True
