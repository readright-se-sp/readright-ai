import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-key"
SIGNATURE = "SUPERSECRETSIGNATURE123"


def valid_body(**overrides: Any) -> dict[str, Any]:
    """The contract's example request."""
    body: dict[str, Any] = {
        "request_id": "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11",
        "video_url": (
            "https://bucket.s3.ap-southeast-1.amazonaws.com/recordings/abc.webm"
            f"?X-Amz-Signature={SIGNATURE}"
        ),
        "passage_title": "Pam's Cat",
        "passage_content": (
            "Pam has a cat. It is on the bed. It can nap. It can sit. “Oh no!” says Pam. "
            "“The cat fell off the bed!” Is the cat sad? No. It is on the mat."
        ),
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's MAI_* shell variables from leaking into tests."""
    for name in list(os.environ):
        if name.upper().startswith("MAI_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: ignore a local server/.env.
    return Settings(_env_file=None, api_key=API_KEY)  # type: ignore[call-arg]


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # raise_server_exceptions=False so unhandled errors surface as real 500 responses.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
