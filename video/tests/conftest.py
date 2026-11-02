import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from video_helpers import FakeAnalyzer, make_client, make_settings

from app.config import Settings

__all__ = ["FakeAnalyzer"]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's MAI_* shell variables from leaking into tests."""
    for name in list(os.environ):
        if name.upper().startswith("MAI_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: ignore a local video/.env.
    return make_settings()


@pytest.fixture
def analyzer() -> FakeAnalyzer:
    return FakeAnalyzer(ready=True)


@pytest.fixture
def client(settings: Settings, analyzer: FakeAnalyzer) -> Iterator[TestClient]:
    with make_client(settings, analyzer) as test_client:
        yield test_client


@pytest.fixture
def webm_file(tmp_path: Path) -> Path:
    path = tmp_path / "recording.webm"
    path.write_bytes(b"")
    return path


@pytest.fixture
def log_records(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog that also works after `create_app` has replaced the root handlers."""
    root = logging.getLogger()
    added = caplog.handler not in root.handlers
    if added:
        root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        if added:
            root.removeHandler(caplog.handler)
