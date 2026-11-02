"""Worker, S3 and ffmpeg readiness checks (respx, moto, monkeypatched shutil.which)."""

import boto3
import boto3.session
import httpx
import pytest
import respx
from admission_helpers import CountingProcessor, FakeStats, build_app, fast_settings, running
from app.readiness import ffmpeg_ready, s3_ready, worker_ready
from audio_helpers import BUCKET, REGION, use_fake_aws
from moto import mock_aws

pytestmark = pytest.mark.anyio

BASE = "http://audio.test:8001"
OK_STATS = {"queue_length": 0, "avg_processing_seconds": None}


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


# Workers ---------------------------------------------------------------------------------------


async def test_worker_is_ready_when_health_and_stats_are_good(router):
    """Criterion 13."""
    ready = router.get(f"{BASE}/health/ready").respond(200, json={"status": "ready"})
    stats = router.get(f"{BASE}/stats").respond(200, json=OK_STATS)
    assert await worker_ready(BASE, fast_settings()) is True
    assert ready.called
    assert stats.called


async def test_worker_is_not_ready_when_health_returns_503(router):
    """Criterion 13."""
    router.get(f"{BASE}/health/ready").respond(503, json={"status": "loading"})
    router.get(f"{BASE}/stats").respond(200, json=OK_STATS)
    assert await worker_ready(BASE, fast_settings()) is False


async def test_worker_is_not_ready_when_stats_connection_fails(router):
    """Criterion 13."""
    router.get(f"{BASE}/health/ready").respond(200, json={"status": "ready"})
    router.get(f"{BASE}/stats").mock(side_effect=httpx.ConnectError("refused"))
    assert await worker_ready(BASE, fast_settings()) is False


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"queue_length": -4, "avg_processing_seconds": 1.0}),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, content=b"<html>"),
        httpx.Response(500),
    ],
)
async def test_worker_is_not_ready_when_stats_body_is_invalid(router, response):
    """Criterion 13."""
    router.get(f"{BASE}/health/ready").respond(200, json={"status": "ready"})
    router.get(f"{BASE}/stats").mock(return_value=response)
    assert await worker_ready(BASE, fast_settings()) is False


async def test_worker_is_not_ready_when_health_endpoint_is_unreachable(router):
    """Criterion 13."""
    router.get(f"{BASE}/health/ready").mock(side_effect=httpx.ConnectError("refused"))
    router.get(f"{BASE}/stats").respond(200, json=OK_STATS)
    assert await worker_ready(BASE, fast_settings()) is False


async def test_worker_error_bodies_and_urls_are_not_echoed_by_readiness(redis, router):
    """Criterion 13."""
    settings = fast_settings(audio_worker_url="http://secret-host.test:8001")
    router.get("http://secret-host.test:8001/health/ready").respond(
        503, text="TRACEBACK-SECRET-BODY"
    )
    router.get("http://secret-host.test:8001/stats").respond(200, json=OK_STATS)

    async def audio_worker() -> bool:
        return await worker_ready(settings.audio_worker_url, settings)

    app = build_app(
        settings, redis, CountingProcessor(), FakeStats(), {"audio_worker": audio_worker}
    )
    async with running(app) as http:
        response = await http.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["details"]["failed_checks"] == ["audio_worker"]
    assert "TRACEBACK-SECRET-BODY" not in response.text
    assert "secret-host" not in response.text


# S3 --------------------------------------------------------------------------------------------


@pytest.fixture
def aws(monkeypatch):
    use_fake_aws(monkeypatch)
    with mock_aws():
        yield boto3.client("s3", region_name=REGION)


async def test_s3_is_ready_when_bucket_exists(aws):
    """Criterion 14."""
    aws.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    assert await s3_ready(fast_settings(s3_bucket=BUCKET, aws_region=REGION)) is True


async def test_s3_is_not_ready_when_bucket_is_missing(aws):
    """Criterion 14."""
    assert await s3_ready(fast_settings(s3_bucket="no-such-bucket", aws_region=REGION)) is False


async def test_s3_is_not_ready_without_bucket_setting_and_makes_no_call(monkeypatch):
    """Criterion 14."""
    use_fake_aws(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("S3 must not be touched when no bucket is configured")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    assert await s3_ready(fast_settings(s3_bucket=None)) is False


async def test_s3_check_uses_configured_region_and_endpoint(monkeypatch):
    """Criterion 14: assumes the client comes from boto3.client or Session.client."""
    use_fake_aws(monkeypatch)
    created: list[dict] = []

    class FakeClient:
        def head_bucket(self, **kwargs):
            created.append({"head_bucket": kwargs})

    def make(*args, **kwargs):
        created.append(kwargs)
        return FakeClient()

    monkeypatch.setattr(boto3, "client", make)
    monkeypatch.setattr(boto3.session.Session, "client", lambda self, *a, **k: make(*a, **k))
    settings = fast_settings(
        s3_bucket=BUCKET, aws_region="eu-west-1", s3_endpoint_url="http://minio.test:9000"
    )
    assert await s3_ready(settings) is True
    client_kwargs = next(c for c in created if "head_bucket" not in c)
    assert client_kwargs["region_name"] == "eu-west-1"
    assert client_kwargs["endpoint_url"] == "http://minio.test:9000"
    assert {"head_bucket": {"Bucket": BUCKET}} in created


async def test_s3_is_not_ready_when_head_bucket_raises(monkeypatch):
    """Criterion 14."""
    use_fake_aws(monkeypatch)

    class BrokenClient:
        def head_bucket(self, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(boto3, "client", lambda *a, **k: BrokenClient())
    monkeypatch.setattr(boto3.session.Session, "client", lambda self, *a, **k: BrokenClient())
    assert await s3_ready(fast_settings(s3_bucket=BUCKET)) is False


# ffmpeg ----------------------------------------------------------------------------------------


async def test_ffmpeg_is_ready_when_both_binaries_are_found(monkeypatch):
    """Criterion 15."""
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert await ffmpeg_ready(fast_settings()) is True


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
async def test_ffmpeg_is_not_ready_when_either_binary_is_missing(monkeypatch, missing):
    """Criterion 15."""
    monkeypatch.setattr(
        "shutil.which", lambda name, *a, **k: None if name == missing else f"/usr/bin/{name}"
    )
    assert await ffmpeg_ready(fast_settings()) is False


async def test_ffmpeg_is_not_ready_for_nonexistent_configured_path():
    """Criterion 15."""
    settings = fast_settings(ffmpeg_path="no-such-ffmpeg-binary-xyz")
    assert await ffmpeg_ready(settings) is False
