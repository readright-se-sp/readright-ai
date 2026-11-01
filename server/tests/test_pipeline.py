"""Phase 3: settings, lifespan wiring, pipeline processor, end to end (criteria 1, 2, 20-22)."""

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
import respx
from conftest import API_KEY, SIGNATURE, valid_body
from pydantic import ValidationError

from app import pipeline
from app.config import Settings
from app.deadline import Deadline, deadline_var
from app.errors import MAIError
from app.main import create_app, stub_processor
from app.media import ProbeResult
from app.models import AnalysisRequest, ErrorCode
from app.pipeline import pipeline_processor
from app.stub import build_stub_analysis

pytestmark = pytest.mark.anyio

REQUEST_ID = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"
KEY = f"mai:req:{REQUEST_ID}"
GOOD_PROBE = ProbeResult("matroska,webm", True, True, 1.0)


def real_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        api_key=API_KEY,
        pipeline_mode="real",
        scratch_dir=tmp_path / "scratch",
        **overrides,
    )


# 1. settings ------------------------------------------------------------------------------------


def test_c1_defaults():
    s = Settings(_env_file=None, api_key="k")  # type: ignore[call-arg]
    assert s.pipeline_mode == "real"
    assert s.scratch_dir is None
    assert s.max_video_bytes == 209_715_200
    assert s.ffmpeg_path == "ffmpeg"
    assert s.ffprobe_path == "ffprobe"


def test_c1_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAI_API_KEY", "k")
    monkeypatch.setenv("MAI_PIPELINE_MODE", "stub")
    monkeypatch.setenv("MAI_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setenv("MAI_MAX_VIDEO_BYTES", "12345")
    monkeypatch.setenv("MAI_FFMPEG_PATH", "/opt/ffmpeg")
    monkeypatch.setenv("MAI_FFPROBE_PATH", "/opt/ffprobe")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.pipeline_mode == "stub"
    assert s.scratch_dir == tmp_path
    assert s.max_video_bytes == 12345
    assert s.ffmpeg_path == "/opt/ffmpeg"
    assert s.ffprobe_path == "/opt/ffprobe"


def test_c1_invalid_pipeline_mode_fails():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="k", pipeline_mode="fake")  # type: ignore[call-arg]


@pytest.mark.parametrize("value", [0, -5])
def test_c1_max_video_bytes_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="k", max_video_bytes=value)  # type: ignore[call-arg]


# 2. lifespan ------------------------------------------------------------------------------------


async def _processor_after_startup(settings: Settings, redis):
    app = create_app(settings, redis=redis)
    async with app.router.lifespan_context(app):
        return app.state.processor


async def test_c2_stub_mode_uses_stub_processor(tmp_path, redis):
    settings = real_settings(tmp_path).model_copy(update={"pipeline_mode": "stub"})
    assert await _processor_after_startup(settings, redis) is stub_processor


async def test_c2_real_mode_uses_pipeline_processor(tmp_path, redis):
    assert await _processor_after_startup(real_settings(tmp_path), redis) is pipeline_processor


async def test_c2_real_mode_warns_when_binaries_missing(tmp_path, redis, monkeypatch, caplog):
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    app = create_app(real_settings(tmp_path), redis=redis)
    root = logging.getLogger()
    root.addHandler(caplog.handler)  # configure_logging replaced the root handlers
    caplog.set_level(logging.DEBUG)
    try:
        async with app.router.lifespan_context(app):
            assert app.state.processor is pipeline_processor  # no crash
    finally:
        root.removeHandler(caplog.handler)
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


# 20. pipeline_processor -------------------------------------------------------------------------


class Stages:
    """Records the stage calls made through the app.pipeline module globals."""

    def __init__(self, scratch: Path, *, size: int = 7):
        self.scratch = scratch
        self.size = size
        self.events: list[tuple] = []

    async def download_video(self, url, dest, *, max_bytes, deadline):
        self.events.append(("download", str(url), dest, max_bytes, deadline))
        assert dest.parent.is_dir()
        dest.write_bytes(b"x" * self.size)
        return self.size

    async def probe(self, path, settings):
        self.events.append(("probe", path))
        assert path.exists()
        return GOOD_PROBE

    async def convert_to_wav(self, src, dest, settings):
        self.events.append(("convert", src, dest))
        dest.write_bytes(b"RIFF")

    def install(self, monkeypatch):
        monkeypatch.setattr(pipeline, "download_video", self.download_video)
        monkeypatch.setattr(pipeline, "probe", self.probe)
        monkeypatch.setattr(pipeline, "convert_to_wav", self.convert_to_wav)


async def test_c20_runs_stages_in_order_inside_scratch_and_returns_stub(tmp_path, monkeypatch):
    settings = real_settings(tmp_path)
    body = AnalysisRequest(**valid_body())
    stages = Stages(tmp_path / "scratch")
    stages.install(monkeypatch)

    result = await pipeline_processor(body, settings)

    assert result == build_stub_analysis(body, settings)
    assert [e[0] for e in stages.events] == ["download", "probe", "convert"]
    _, url, dest, max_bytes, _ = stages.events[0]
    request_dir = tmp_path / "scratch" / REQUEST_ID
    assert url == str(body.video_url)
    assert dest == request_dir / "input.webm"
    assert max_bytes == settings.max_video_bytes
    assert stages.events[1][1] == request_dir / "input.webm"
    assert stages.events[2][1:] == (request_dir / "input.webm", request_dir / "audio.wav")
    assert not request_dir.exists()


async def test_c20_uses_deadline_from_context_var(tmp_path, monkeypatch):
    stages = Stages(tmp_path)
    stages.install(monkeypatch)
    mine = Deadline(50.0)
    token = deadline_var.set(mine)
    try:
        await pipeline_processor(AnalysisRequest(**valid_body()), real_settings(tmp_path))
    finally:
        deadline_var.reset(token)
    assert stages.events[0][4] is mine


async def test_c20_creates_deadline_when_context_var_unset(tmp_path, monkeypatch):
    stages = Stages(tmp_path)
    stages.install(monkeypatch)
    assert deadline_var.get() is None
    settings = real_settings(tmp_path, request_deadline_seconds=42.0)
    await pipeline_processor(AnalysisRequest(**valid_body()), settings)
    created = stages.events[0][4]
    assert isinstance(created, Deadline)
    assert 41.0 < created.remaining() <= 42.0


async def test_c20_scratch_removed_when_a_stage_fails(tmp_path, monkeypatch):
    stages = Stages(tmp_path)
    stages.install(monkeypatch)

    async def bad_probe(path, settings):
        raise MAIError(ErrorCode.UNSUPPORTED_MEDIA, "no")

    monkeypatch.setattr(pipeline, "probe", bad_probe)
    with pytest.raises(MAIError):
        await pipeline_processor(AnalysisRequest(**valid_body()), real_settings(tmp_path))
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


# 21. stage logging ------------------------------------------------------------------------------


async def test_c21_each_stage_logs_stage_finished(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    Stages(tmp_path, size=7).install(monkeypatch)
    await pipeline_processor(AnalysisRequest(**valid_body()), real_settings(tmp_path))

    finished = [r for r in caplog.records if r.getMessage() == "stage finished"]
    assert all(r.levelno == logging.INFO for r in finished)
    by_stage = {r.stage: r for r in finished}  # type: ignore[attr-defined]
    assert set(by_stage) == {"download", "probe", "convert"}
    assert len(finished) == 3
    for record in finished:
        assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]
        assert record.duration_ms >= 0  # type: ignore[attr-defined]
    assert by_stage["download"].bytes == 7  # type: ignore[attr-defined]


# 22. end to end ---------------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Overrides the conftest fixture: real pipeline mode with a per-test scratch dir."""
    return real_settings(tmp_path, request_deadline_seconds=0.5, in_progress_ttl_seconds=130)


@pytest.fixture
def s3():
    with respx.mock(assert_all_called=False) as router:
        yield router.get(url__regex=r"https://bucket\.s3\.ap-southeast-1\.amazonaws\.com/.*")


@pytest.fixture
def stub_media(monkeypatch):
    async def fake_probe(path, settings):
        return GOOD_PROBE

    async def fake_convert(src, dest, settings):
        dest.write_bytes(b"RIFF")

    monkeypatch.setattr(pipeline, "probe", fake_probe)
    monkeypatch.setattr(pipeline, "convert_to_wav", fake_convert)


async def post(client, auth):
    return await client.post("/v1/analyses", json=valid_body(), headers=auth)


def code(response) -> str:
    return response.json()["error"]["code"]


async def assert_clean_and_retryable(client, auth, redis, tmp_path, s3):
    """Scratch dir is gone, no lease is stuck, and a retry gets processed."""
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()
    assert await redis.exists(KEY) == 0
    s3.respond(200, content=b"video-bytes")
    retry = await post(client, auth)
    assert retry.status_code == 200
    assert "X-MAI-Replayed" not in retry.headers
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_c22_success_returns_stub_body(app, async_client, auth, s3, stub_media, tmp_path):
    assert app.state.settings.pipeline_mode == "real"
    s3.respond(200, content=b"video-bytes")
    response = await post(async_client, auth)
    assert response.status_code == 200
    assert response.headers["X-MAI-Stub"] == "true"
    body = AnalysisRequest(**valid_body())
    expected = build_stub_analysis(body, app.state.settings).model_dump(mode="json")
    assert response.json() == expected
    assert not (tmp_path / "scratch" / REQUEST_ID).exists()


async def test_c22_expired_url_is_422(app, async_client, auth, redis, s3, stub_media, tmp_path):
    s3.respond(403, content=b"<Message>Request has expired</Message>")
    response = await post(async_client, auth)
    assert response.status_code == 422
    assert code(response) == "VIDEO_URL_EXPIRED"
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, s3)


async def test_c22_not_found_is_download_failed(
    app, async_client, auth, redis, s3, stub_media, tmp_path, caplog
):
    root = logging.getLogger()
    root.addHandler(caplog.handler)  # configure_logging replaced the root handlers
    caplog.set_level(logging.DEBUG)
    try:
        s3.respond(404)
        response = await post(async_client, auth)
        assert response.status_code == 422
        assert code(response) == "VIDEO_DOWNLOAD_FAILED"
        assert response.json()["error"]["details"] == {"upstream_status": 404}
        await assert_clean_and_retryable(async_client, auth, redis, tmp_path, s3)
    finally:
        root.removeHandler(caplog.handler)
    # c14 end to end: no log record or response carries the presigned query string.
    logged = "\n".join(r.getMessage() + " " + str(r.__dict__) for r in caplog.records)
    assert SIGNATURE not in logged
    assert SIGNATURE not in response.text


async def test_c22_probe_415_propagates(
    app, async_client, auth, redis, s3, monkeypatch, stub_media, tmp_path
):
    async def reject(path, settings):
        raise MAIError(ErrorCode.UNSUPPORTED_MEDIA, "not webm")

    monkeypatch.setattr(pipeline, "probe", reject)
    s3.respond(200, content=b"video-bytes")
    response = await post(async_client, auth)
    assert response.status_code == 415
    assert code(response) == "UNSUPPORTED_MEDIA"

    async def accept(path, settings):
        return GOOD_PROBE

    monkeypatch.setattr(pipeline, "probe", accept)
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, s3)


async def test_c22_slow_download_past_deadline_is_504(
    app, async_client, auth, redis, s3, stub_media, tmp_path
):
    async def slow(request):
        await asyncio.sleep(10)
        return httpx.Response(200, content=b"late")

    s3.mock(side_effect=slow)
    response = await post(async_client, auth)
    assert response.status_code == 504
    assert code(response) == "TIMEOUT"
    s3.mock(side_effect=None)
    await assert_clean_and_retryable(async_client, auth, redis, tmp_path, s3)
