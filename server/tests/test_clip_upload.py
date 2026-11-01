"""Uploading one cut clip per miscue to S3 (moto, plus fake clients for call-level checks).

Assumption for the fake-client tests: `app.upload` creates its S3 client through boto3, either
`boto3.client(...)` or `boto3.session.Session(...).client(...)`. Both entry points are patched to
return the fake, and the keyword arguments of the call are recorded (`region_name`,
`endpoint_url`, `config`).
"""

import asyncio
import logging
import threading
import time

import boto3
import boto3.session
import pytest
from audio_helpers import (
    BUCKET,
    REGION,
    expected_range,
    make_response,
    read_wav_bytes,
    use_fake_aws,
    write_wav,
)
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from lane_helpers import worker_settings
from moto import mock_aws

from app.deadline import Deadline
from app.errors import MAIError
from app.models import ErrorCode
from app.upload import upload_clips

pytestmark = pytest.mark.anyio

DURATION_MS = 3000
SPANS = [(100, 300), (1000, 1400), (2900, 3000)]


@pytest.fixture
def aws(monkeypatch):
    use_fake_aws(monkeypatch)
    with mock_aws():
        yield boto3.client("s3", region_name=REGION)


@pytest.fixture
def bucket(aws):
    aws.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    return aws


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "audio.wav"
    return path, write_wav(path, DURATION_MS)


def make_settings(tmp_path, **overrides):
    return worker_settings(tmp_path, s3_bucket=BUCKET, **overrides)


class FakeS3:
    """Stands in for a boto3 S3 client; records calls and can sleep or fail in put_object."""

    def __init__(self, delay: float = 0.0, error: Exception | None = None):
        self.delay = delay
        self.error = error
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.client_calls: list[dict] = []

    def put_object(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return {}
        finally:
            with self.lock:
                self.in_flight -= 1

    def __getattr__(self, name):  # close() and friends
        return lambda *args, **kwargs: None


@pytest.fixture
def fake_s3(monkeypatch):
    use_fake_aws(monkeypatch)
    fake = FakeS3()

    def client(*args, **kwargs):
        fake.client_calls.append(kwargs)
        return fake

    monkeypatch.setattr(boto3, "client", client)
    monkeypatch.setattr(boto3.session.Session, "client", lambda self, *a, **k: client(*a, **k))
    return fake


def deadline(seconds: float = 30.0) -> Deadline:
    return Deadline(seconds)


def fetch(aws, key):
    obj = aws.get_object(Bucket=BUCKET, Key=key)
    return obj["Body"].read(), obj["ContentType"]


async def test_uploads_each_padded_clip_at_its_key(bucket, wav, tmp_path):
    # criterion 6
    path, pcm = wav
    response = make_response(SPANS)

    await upload_clips(response, path, make_settings(tmp_path), deadline())

    listed = bucket.list_objects_v2(Bucket=BUCKET)["Contents"]
    assert sorted(o["Key"] for o in listed) == sorted(m.audio_object_key for m in response.miscues)
    for miscue in response.miscues:
        data, content_type = fetch(bucket, miscue.audio_object_key)
        assert content_type == "audio/wav"
        params, frames = read_wav_bytes(data, tmp_path)
        assert (params.framerate, params.nchannels, params.sampwidth) == (16000, 1, 2)
        first, last = expected_range(DURATION_MS, miscue.read_start, miscue.read_end, 150)
        assert frames == pcm[first * 2 : last * 2]


async def test_clip_padding_comes_from_settings(bucket, wav, tmp_path):
    # criterion 6
    path, pcm = wav
    response = make_response([(1000, 1400)])

    await upload_clips(response, path, make_settings(tmp_path, clip_padding_ms=0), deadline())

    data, _ = fetch(bucket, response.miscues[0].audio_object_key)
    params, frames = read_wav_bytes(data, tmp_path)
    assert params.nframes == 400 * 16
    assert frames == pcm[1000 * 16 * 2 : 1400 * 16 * 2]


async def test_response_times_are_not_changed_by_padding(bucket, wav, tmp_path):
    # criterion 6 / D2
    path, _ = wav
    response = make_response(SPANS)
    await upload_clips(response, path, make_settings(tmp_path), deadline())
    assert [(m.read_start, m.read_end) for m in response.miscues] == SPANS


async def test_put_object_receives_bucket_key_body_and_content_type(fake_s3, wav, tmp_path):
    # criterion 6
    path, _ = wav
    response = make_response([(1000, 1400)])

    await upload_clips(response, path, make_settings(tmp_path), deadline())

    [call] = fake_s3.calls
    assert call["Bucket"] == BUCKET
    assert call["Key"] == response.miscues[0].audio_object_key
    assert call["ContentType"] == "audio/wav"
    assert call["Body"][:4] == b"RIFF"


async def test_client_uses_configured_region_and_endpoint(fake_s3, wav, tmp_path):
    # criterion 6
    path, _ = wav
    settings = make_settings(tmp_path, s3_endpoint_url="http://localhost:9000")
    await upload_clips(make_response([(1000, 1400)]), path, settings, deadline())
    [kwargs] = fake_s3.client_calls
    assert kwargs["region_name"] == REGION
    assert kwargs["endpoint_url"] == "http://localhost:9000"


async def test_client_has_no_endpoint_when_unset(fake_s3, wav, tmp_path):
    # criterion 6
    path, _ = wav
    await upload_clips(make_response([(1000, 1400)]), path, make_settings(tmp_path), deadline())
    [kwargs] = fake_s3.client_calls
    assert kwargs.get("endpoint_url") is None


async def test_zero_miscues_makes_no_client_and_no_call(fake_s3, wav, tmp_path):
    # criterion 7: works with the bucket unset
    path, _ = wav
    settings = worker_settings(tmp_path)
    assert settings.s3_bucket is None

    await upload_clips(make_response([]), path, settings, deadline())

    assert fake_s3.client_calls == []
    assert fake_s3.calls == []


async def test_miscues_without_a_bucket_fail_and_call_nothing(fake_s3, wav, tmp_path):
    # criterion 8
    path, _ = wav
    with pytest.raises(MAIError) as raised:
        await upload_clips(make_response(SPANS), path, worker_settings(tmp_path), deadline())
    assert raised.value.code == ErrorCode.UPLOAD_FAILED
    assert fake_s3.calls == []


async def test_in_flight_uploads_never_exceed_the_concurrency_setting(fake_s3, wav, tmp_path):
    # criterion 9
    path, _ = wav
    fake_s3.delay = 0.05
    response = make_response([(i * 300, i * 300 + 200) for i in range(8)])

    await upload_clips(response, path, make_settings(tmp_path, upload_concurrency=2), deadline())

    assert len(fake_s3.calls) == 8
    assert fake_s3.max_in_flight == 2


async def test_uploads_run_concurrently_when_allowed(fake_s3, wav, tmp_path):
    # criterion 9: the limit is a cap, not serialisation
    path, _ = wav
    fake_s3.delay = 0.05
    response = make_response([(i * 300, i * 300 + 200) for i in range(6)])

    await upload_clips(response, path, make_settings(tmp_path, upload_concurrency=6), deadline())

    assert fake_s3.max_in_flight > 1


async def test_missing_bucket_in_s3_is_upload_failed(aws, wav, tmp_path, log_records):
    # criterion 10: bucket never created
    path, _ = wav
    response = make_response(SPANS)
    with pytest.raises(MAIError) as raised:
        await upload_clips(response, path, make_settings(tmp_path), deadline())
    assert raised.value.code == ErrorCode.UPLOAD_FAILED
    assert raised.value.status == 500


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject"),
        NoCredentialsError(),
        EndpointConnectionError(endpoint_url="http://localhost:9"),
    ],
    ids=["client-error", "no-credentials", "endpoint-connection"],
)
async def test_botocore_errors_become_upload_failed(fake_s3, wav, tmp_path, error):
    # criterion 10
    path, _ = wav
    fake_s3.error = error
    with pytest.raises(MAIError) as raised:
        await upload_clips(make_response(SPANS), path, make_settings(tmp_path), deadline())
    assert raised.value.code == ErrorCode.UPLOAD_FAILED


async def test_failure_is_logged_with_key_and_error_but_no_credentials(
    monkeypatch, aws, wav, tmp_path, log_records
):
    # criterion 10
    use_fake_aws(monkeypatch, key_id="AKIAFAKEKEYID12345", secret="fakeSecretValueXYZ987")
    path, _ = wav
    response = make_response([(1000, 1400)])
    key = response.miscues[0].audio_object_key

    with pytest.raises(MAIError):
        await upload_clips(response, path, make_settings(tmp_path), deadline())

    app_records = [r for r in log_records.records if r.name.startswith("app")]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in app_records)
    assert key in text
    assert "NoSuchBucket" in text or "ClientError" in text
    assert "AKIAFAKEKEYID12345" not in text
    assert "fakeSecretValueXYZ987" not in text
    assert any(r.levelno >= logging.WARNING for r in app_records)


async def test_first_failure_stops_pending_uploads(fake_s3, wav, tmp_path):
    # criterion 10: with one slot, the failed upload must prevent every later one
    path, _ = wav
    fake_s3.error = ClientError({"Error": {"Code": "SlowDown", "Message": "x"}}, "PutObject")
    response = make_response([(i * 300, i * 300 + 200) for i in range(6)])

    with pytest.raises(MAIError):
        await upload_clips(
            response, path, make_settings(tmp_path, upload_concurrency=1), deadline()
        )

    assert len(fake_s3.calls) == 1


@pytest.mark.parametrize(("seconds", "limit"), [(30.0, 30.0), (0.5, 1.0)])
async def test_client_timeouts_fit_the_deadline_with_a_one_second_floor(
    fake_s3, wav, tmp_path, seconds, limit
):
    # criterion 11
    path, _ = wav
    await upload_clips(
        make_response([(1000, 1400)]), path, make_settings(tmp_path), Deadline(seconds)
    )
    config = fake_s3.client_calls[0]["config"]
    assert config.connect_timeout <= limit
    assert config.read_timeout <= limit
    assert config.retries["max_attempts"] <= 2


async def test_deadline_expiry_while_uploading_is_timeout(fake_s3, wav, tmp_path):
    # criterion 11: the slow PUT keeps running in its thread; the call itself returns at once
    path, _ = wav
    fake_s3.delay = 1.5
    started = time.monotonic()

    with pytest.raises(MAIError) as raised:
        await upload_clips(
            make_response([(1000, 1400)]), path, make_settings(tmp_path), Deadline(0.2)
        )

    assert raised.value.code == ErrorCode.TIMEOUT
    assert raised.value.status == 504
    assert time.monotonic() - started < 1.2
    await asyncio.sleep(1.5)  # let the background thread finish before the test ends


async def test_retry_overwrites_clips_at_the_same_keys(bucket, tmp_path):
    # criterion 15
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    write_wav(first, DURATION_MS, offset=0)
    pcm_second = write_wav(second, DURATION_MS, offset=7000)
    response = make_response(SPANS)
    settings = make_settings(tmp_path)

    await upload_clips(response, first, settings, deadline())
    await upload_clips(response, second, settings, deadline())

    listed = bucket.list_objects_v2(Bucket=BUCKET)["Contents"]
    assert sorted(o["Key"] for o in listed) == sorted(m.audio_object_key for m in response.miscues)
    for miscue in response.miscues:
        data, _ = fetch(bucket, miscue.audio_object_key)
        _, frames = read_wav_bytes(data, tmp_path)
        first_frame, last_frame = expected_range(
            DURATION_MS, miscue.read_start, miscue.read_end, 150
        )
        assert frames == pcm_second[first_frame * 2 : last_frame * 2]
