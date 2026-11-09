"""Repeated /health/ready probes with the default dependency checks; malformed worker URLs."""

import logging

import boto3
import boto3.session
import httpx
import pytest
import respx
from admission_helpers import CountingProcessor, fast_settings, running
from audio_helpers import BUCKET, REGION, use_fake_aws
from conftest import idle_worker_stats
from moto import mock_aws

from app.main import create_app
from app.readiness import worker_ready

pytestmark = pytest.mark.anyio

AUDIO = "http://audio.test:8001"
VIDEO = "http://video.test:8002"
OK_STATS = {"queue_length": 0, "avg_processing_seconds": None}


@pytest.fixture
def world(monkeypatch):
    """Healthy workers (respx), an existing bucket (moto) and found ffmpeg binaries."""
    use_fake_aws(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    with mock_aws(), respx.mock(assert_all_called=False) as router:
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        router.audio_ready = router.get(f"{AUDIO}/health/ready").respond(200, json={})
        router.video_ready = router.get(f"{VIDEO}/health/ready").respond(200, json={})
        router.get(f"{AUDIO}/stats").respond(200, json=OK_STATS)
        router.get(f"{VIDEO}/stats").respond(200, json=OK_STATS)
        router.s3 = s3
        yield router


def make_app(redis):
    return create_app(
        fast_settings(s3_bucket=BUCKET, aws_region=REGION),
        redis=redis,
        processor=CountingProcessor(),
        worker_stats=idle_worker_stats,
    )


async def test_repeated_probes_are_all_ready(redis, world):
    """Criterion 22."""
    async with running(make_app(redis)) as http:
        for _ in range(3):
            response = await http.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["checks"] == {
                "redis": True,
                "audio_worker": True,
                "video_worker": True,
                "s3": True,
                "ffmpeg": True,
            }


async def test_probe_results_are_not_cached_between_probes(redis, world):
    """Criterion 22: a dependency going down, and coming back, shows on the very next probe."""
    async with running(make_app(redis)) as http:
        assert (await http.get("/health/ready")).status_code == 200

        world.audio_ready.respond(503)
        down = await http.get("/health/ready")
        assert down.status_code == 503
        assert down.json()["error"]["details"]["failed_checks"] == ["audio_worker"]

        world.audio_ready.respond(200, json={})
        assert (await http.get("/health/ready")).status_code == 200

        world.s3.delete_bucket(Bucket=BUCKET)
        s3_down = await http.get("/health/ready")
        assert s3_down.status_code == 503
        assert s3_down.json()["error"]["details"]["failed_checks"] == ["s3"]


async def test_s3_client_is_not_rebuilt_for_every_probe(redis, world, monkeypatch):
    """Criterion 22: counts boto3 client constructions (assumes boto3 is the entry point)."""
    created = []
    real_session_client = boto3.session.Session.client

    def counting_session_client(self, *args, **kwargs):
        created.append(1)  # boto3.client() delegates here, so this counts both entry points
        return real_session_client(self, *args, **kwargs)

    monkeypatch.setattr(boto3.session.Session, "client", counting_session_client)
    async with running(make_app(redis)) as http:
        for _ in range(3):
            assert (await http.get("/health/ready")).status_code == 200
    assert len(created) <= 1


@pytest.mark.parametrize("url", ["http://[::1", "http://[bad", "not a url at all", ""])
async def test_malformed_worker_url_is_not_ready_and_does_not_raise(url, caplog):
    """Criterion 21."""
    root = logging.getLogger()
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        assert await worker_ready(url, fast_settings()) is False
    finally:
        root.removeHandler(caplog.handler)
    for record in caplog.records:
        text = " ".join(
            [record.getMessage(), record.exc_text or "", str(record.exc_info), str(record.args)]
            + [str(v) for v in record.__dict__.values()]
        )
        if url:
            assert url not in text
        assert "Traceback" not in text
    assert httpx  # imported for parity with the other readiness tests
