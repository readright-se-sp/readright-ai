"""Every upload failure is mapped to a MAIError; a deadline hit during upload is logged."""

import time

import boto3
import boto3.session
import pytest
from audio_helpers import BUCKET, make_response, use_fake_aws, write_wav
from lane_helpers import worker_settings

from app.deadline import Deadline
from app.errors import MAIError
from app.models import ErrorCode
from app.upload import upload_clips

pytestmark = pytest.mark.anyio

SPANS = [(100, 300), (1000, 1400), (1800, 2000), (2200, 2400)]


class FakeS3:
    def __init__(self, delay: float = 0.0, error: Exception | None = None):
        self.delay = delay
        self.error = error
        self.calls = 0

    def put_object(self, **kwargs):
        self.calls += 1
        time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return {}

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture
def fake_s3(monkeypatch):
    """Patches boto3.client and Session.client (see test_clip_upload for the assumption)."""
    use_fake_aws(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    monkeypatch.setattr(boto3.session.Session, "client", lambda self, *a, **k: fake)
    return fake


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "audio.wav"
    write_wav(path, 3000)
    return path


def settings_for(tmp_path, **overrides):
    return worker_settings(tmp_path, s3_bucket=BUCKET, **overrides)


async def test_missing_wav_is_processing_failed_in_audio_lane(fake_s3, tmp_path):
    # criterion 19
    with pytest.raises(MAIError) as raised:
        await upload_clips(
            make_response(SPANS), tmp_path / "missing.wav", settings_for(tmp_path), Deadline(30)
        )
    assert raised.value.code == ErrorCode.PROCESSING_FAILED
    assert raised.value.details == {"lane": "audio"}


@pytest.mark.parametrize("content", [b"RIFF", b"", b"not a wav file at all"])
async def test_corrupt_wav_is_processing_failed_in_audio_lane(fake_s3, tmp_path, content):
    # criterion 19
    bad = tmp_path / "bad.wav"
    bad.write_bytes(content)
    with pytest.raises(MAIError) as raised:
        await upload_clips(make_response(SPANS), bad, settings_for(tmp_path), Deadline(30))
    assert raised.value.code == ErrorCode.PROCESSING_FAILED
    assert raised.value.details == {"lane": "audio"}


async def test_unexpected_upload_error_is_upload_failed(fake_s3, wav, tmp_path):
    # criterion 19
    fake_s3.error = RuntimeError("boom")
    with pytest.raises(MAIError) as raised:
        await upload_clips(make_response(SPANS), wav, settings_for(tmp_path), Deadline(30))
    assert raised.value.code == ErrorCode.UPLOAD_FAILED


async def test_simultaneous_failures_are_one_mai_error_not_an_exception_group(
    fake_s3, wav, tmp_path
):
    # criterion 19: all four uploads fail at once, so a TaskGroup would raise an ExceptionGroup
    fake_s3.error = RuntimeError("boom")
    fake_s3.delay = 0.05
    with pytest.raises(MAIError) as raised:
        await upload_clips(
            make_response(SPANS), wav, settings_for(tmp_path, upload_concurrency=4), Deadline(30)
        )
    assert raised.value.code == ErrorCode.UPLOAD_FAILED


async def test_unexpected_error_is_logged_by_class_name_only(fake_s3, wav, tmp_path, log_records):
    # criterion 19
    fake_s3.error = RuntimeError("secret-detail-12345")
    with pytest.raises(MAIError):
        await upload_clips(make_response(SPANS), wav, settings_for(tmp_path), Deadline(30))
    text = " ".join(
        f"{r.getMessage()} {r.__dict__}" for r in log_records.records if r.name.startswith("app")
    )
    assert "RuntimeError" in text
    assert "secret-detail-12345" not in text


async def test_deadline_hit_during_upload_logs_warning_with_clip_count(
    fake_s3, wav, tmp_path, log_records
):
    # criterion 20: behaviour is unchanged (TIMEOUT) and the timeout is logged
    fake_s3.delay = 1.0
    with pytest.raises(MAIError) as raised:
        await upload_clips(make_response(SPANS), wav, settings_for(tmp_path), Deadline(0.2))
    assert raised.value.code == ErrorCode.TIMEOUT
    [record] = [r for r in log_records.records if r.getMessage() == "clip upload timed out"]
    assert record.levelname == "WARNING"
    assert record.clips == len(SPANS)
    time.sleep(1.0)  # let the background PUT threads finish


async def test_pending_uploads_do_not_start_after_first_failure(fake_s3, wav, tmp_path):
    # criterion 20: criterion 10 still holds without the old flag
    fake_s3.error = RuntimeError("boom")
    with pytest.raises(MAIError):
        await upload_clips(
            make_response(SPANS), wav, settings_for(tmp_path, upload_concurrency=1), Deadline(30)
        )
    assert fake_s3.calls == 1
