from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!reset", lambda loader, node: None)


def load(name: str) -> dict[str, Any]:
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=_Loader)


def test_audio_worker_is_the_real_image_with_a_gpu() -> None:
    svc = load("compose.yaml")["services"]["audio-worker"]
    assert svc["build"] == "./audio"
    assert svc["image"] == "readright-mai-audio"
    assert "command" not in svc  # the Dockerfile CMD starts the worker
    device = svc["deploy"]["resources"]["reservations"]["devices"][0]
    assert device["driver"] == "nvidia"
    assert "gpu" in device["capabilities"]
    assert svc["environment"]["AUDIO_PHONETIC_ENABLED"] == "${AUDIO_PHONETIC_ENABLED:-false}"
    assert svc["healthcheck"]["start_period"] == "180s"
    assert "/scratch:ro" in svc["volumes"][0]


def test_server_waits_for_the_audio_worker() -> None:
    api = load("compose.yaml")["services"]["api"]
    assert api["depends_on"]["audio-worker"]["condition"] == "service_healthy"
    assert api["environment"]["MAI_AUDIO_WORKER_URL"] == "http://audio-worker:8001"


def test_fake_audio_override_restores_the_fake_worker() -> None:
    svc = load("compose.fake-audio.yaml")["services"]["audio-worker"]
    assert svc["build"] == "./server"
    assert "devtools.fake_workers:audio_app" in svc["command"]
    assert svc["deploy"] is None  # !reset: no GPU reservation


def test_the_other_services_are_unchanged() -> None:
    services = load("compose.yaml")["services"]
    assert set(services) == {"caddy", "api", "redis", "audio-worker", "video-worker"}
    assert services["video-worker"]["build"] == "./video"
