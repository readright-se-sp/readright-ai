"""Settings and startup warning for the S3 clip upload."""

import logging

import pytest
from conftest import API_KEY, fake_processor
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app

WARNING = "MAI_S3_BUCKET is not set; requests with miscues will fail with UPLOAD_FAILED"


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, api_key=API_KEY, **kwargs)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _no_aws_region(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)


def test_defaults():
    # criterion 1
    settings = make()
    assert settings.s3_bucket is None
    assert settings.s3_endpoint_url is None
    assert settings.aws_region == "ap-southeast-1"
    assert settings.upload_concurrency == 8
    assert settings.clip_padding_ms == 150


def test_read_from_environment(monkeypatch):
    # criterion 1
    monkeypatch.setenv("MAI_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("MAI_S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("MAI_UPLOAD_CONCURRENCY", "3")
    monkeypatch.setenv("MAI_CLIP_PADDING_MS", "0")
    settings = make()
    assert settings.s3_bucket == "my-bucket"
    assert settings.s3_endpoint_url == "http://localhost:9000"
    assert settings.aws_region == "us-west-2"
    assert settings.upload_concurrency == 3
    assert settings.clip_padding_ms == 0


def test_region_ignores_prefixed_variable(monkeypatch):
    # criterion 1: MAI_AWS_REGION is not used
    monkeypatch.setenv("MAI_AWS_REGION", "eu-west-1")
    assert make().aws_region == "ap-southeast-1"


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_blank_bucket_is_treated_as_unset(monkeypatch, value):
    # criterion 1
    monkeypatch.setenv("MAI_S3_BUCKET", value)
    assert make().s3_bucket is None


@pytest.mark.parametrize("value", ["0", "-1"])
def test_rejects_non_positive_concurrency(monkeypatch, value):
    # criterion 1
    monkeypatch.setenv("MAI_UPLOAD_CONCURRENCY", value)
    with pytest.raises(ValidationError):
        make()


def test_rejects_negative_padding(monkeypatch):
    # criterion 1
    monkeypatch.setenv("MAI_CLIP_PADDING_MS", "-1")
    with pytest.raises(ValidationError):
        make()


async def _startup_warnings(settings, redis, log_records) -> list[str]:
    app = create_app(settings, redis=redis, processor=fake_processor)
    # create_app replaces the root handlers; put the capture handler back.
    logging.getLogger().addHandler(log_records.handler)
    async with app.router.lifespan_context(app):
        pass
    return [
        r.getMessage()
        for r in log_records.records
        if r.levelno == logging.WARNING and r.getMessage() == WARNING
    ]


@pytest.mark.anyio
async def test_startup_warns_when_bucket_is_unset(redis, log_records):
    # criterion 2
    assert await _startup_warnings(make(), redis, log_records) == [WARNING]


@pytest.mark.anyio
async def test_startup_does_not_warn_when_bucket_is_set(redis, log_records):
    # criterion 2
    assert await _startup_warnings(make(s3_bucket="b"), redis, log_records) == []
