"""Worker settings, app factory wiring and removal of the stub (criteria 1, 2, 3)."""

import importlib.util

import pytest
from conftest import API_KEY, fake_processor, valid_body
from pydantic import ValidationError

import app.main
from app.config import Settings
from app.main import create_app
from app.pipeline import pipeline_processor


def make(**overrides) -> Settings:
    return Settings(_env_file=None, api_key=API_KEY, **overrides)  # type: ignore[call-arg]


def test_worker_defaults():
    """criterion 1"""
    s = make()
    assert s.audio_worker_url == "http://localhost:8001"
    assert s.video_worker_url == "http://localhost:8002"
    assert s.worker_connect_timeout_seconds == 5.0


def test_worker_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("MAI_API_KEY", API_KEY)
    monkeypatch.setenv("MAI_AUDIO_WORKER_URL", "http://audio:9001")
    monkeypatch.setenv("MAI_VIDEO_WORKER_URL", "http://video:9002")
    monkeypatch.setenv("MAI_WORKER_CONNECT_TIMEOUT_SECONDS", "1.5")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.audio_worker_url == "http://audio:9001"
    assert s.video_worker_url == "http://video:9002"
    assert s.worker_connect_timeout_seconds == 1.5


@pytest.mark.parametrize("value", [0, -1.0])
def test_worker_connect_timeout_must_be_positive(value):
    with pytest.raises(ValidationError):
        make(worker_connect_timeout_seconds=value)


def test_pipeline_mode_setting_is_removed(monkeypatch):
    assert "pipeline_mode" not in Settings.model_fields
    monkeypatch.setenv("MAI_PIPELINE_MODE", "stub")
    assert not hasattr(make(), "pipeline_mode")


@pytest.mark.anyio
async def test_injected_processor_becomes_app_state_processor(settings, redis):
    """criterion 2"""
    app_ = create_app(settings, redis=redis, processor=fake_processor)
    async with app_.router.lifespan_context(app_):
        assert app_.state.processor is fake_processor


@pytest.mark.anyio
async def test_pipeline_processor_is_the_default_processor(settings, redis):
    """criterion 2"""
    app_ = create_app(settings, redis=redis)
    async with app_.router.lifespan_context(app_):
        assert app_.state.processor is pipeline_processor


def test_stub_module_and_processor_are_gone():
    """criterion 3"""
    assert importlib.util.find_spec("app.stub") is None
    assert not hasattr(app.main, "stub_processor")


def test_no_response_carries_the_stub_header(client, auth):
    """criterion 3: fresh and replayed."""
    first = client.post("/v1/analyses", json=valid_body(), headers=auth)
    second = client.post("/v1/analyses", json=valid_body(), headers=auth)
    assert first.status_code == second.status_code == 200
    assert second.headers["X-MAI-Replayed"] == "true"
    assert "X-MAI-Stub" not in first.headers
    assert "X-MAI-Stub" not in second.headers
