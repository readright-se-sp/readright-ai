import json
import os
import re
import subprocess

import pytest
from packaging_helpers import ROOT, as_text, docker_ready, load_yaml

from app.config import Settings


@pytest.fixture
def services():
    return load_yaml(ROOT / "compose.yaml")["services"]


@pytest.fixture
def worker(services):
    return services["video-worker"]


def test_video_worker_builds_the_video_directory_into_its_own_image(worker, services):
    """Criterion 8."""
    build = worker["build"]
    context = build if isinstance(build, str) else build["context"]
    assert context.rstrip("/").removeprefix("./") == "video"
    assert worker.get("image")
    assert worker["image"] != services["api"]["image"]
    assert worker["image"] != services["audio-worker"]["image"]


def test_video_worker_uses_the_image_default_command(worker):
    """Criterion 8."""
    assert "command" not in worker
    assert "entrypoint" not in worker


def test_video_worker_mounts_scratch_read_only(worker):
    """Criterion 8."""
    mounts = [v for v in worker["volumes"] if str(v).split(":")[1:2] == ["/scratch"]]
    assert mounts == ["scratch:/scratch:ro"]


def test_video_worker_healthcheck_probes_the_ready_endpoint(worker):
    """Criterion 8."""
    assert "/health/ready" in as_text(worker["healthcheck"]["test"])


def test_video_worker_restarts_unless_stopped_and_publishes_no_ports(worker):
    """Criterion 8."""
    assert worker["restart"] == "unless-stopped"
    assert not worker.get("ports")
    assert not worker.get("expose") or "8002" in [str(p) for p in worker["expose"]]


def test_video_worker_cpu_limit_comes_from_video_cpus_with_default_four(worker):
    """Criterion 8."""
    assert str(worker["cpus"]).replace(" ", "") == "${MAI_VIDEO_CPUS:-4}"


def test_api_waits_for_the_healthy_video_worker(services):
    """Criterion 8."""
    depends = services["api"]["depends_on"]
    assert depends["video-worker"]["condition"] == "service_healthy"


def test_env_example_lists_the_video_cpu_setting():
    """Criterion 8."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^MAI_VIDEO_CPUS=\d+", text, re.MULTILINE)


def test_video_worker_passes_through_only_real_settings_without_values(worker):
    """Unset passthroughs keep the image default, so compose holds no second copy of a default."""
    names = {f"MAI_VIDEO_{name.upper()}" for name in Settings.model_fields}
    env = worker["environment"]
    assert isinstance(env, list) and env
    for entry in env:
        assert "=" not in entry, entry
        assert entry in names, entry
    for entry in ("MAI_VIDEO_MODEL_DIR", "MAI_VIDEO_ALLOWED_MEDIA_DIR"):
        assert entry not in env


def test_env_example_lists_every_video_worker_passthrough(worker):
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing = [name for name in worker["environment"] if f"{name}=" not in text]
    assert not missing


def test_loadtest_overlay_publishes_the_video_worker_on_loopback_only():
    services = load_yaml(ROOT / "compose.loadtest.yaml")["services"]
    assert set(services) == {"video-worker"}
    assert services["video-worker"]["ports"] == ["127.0.0.1:8002:8002"]


docker_only = pytest.mark.skipif(not docker_ready(), reason="docker daemon not available")


def compose_config(*files: str, extra_env: dict[str, str] | None = None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MAI_")}
    env.pop("AWS_ACCESS_KEY_ID", None)
    env.pop("AWS_SECRET_ACCESS_KEY", None)
    env["MAI_API_KEY"] = "dummy-not-a-real-key"
    env.update(extra_env or {})
    command = ["docker", "compose"]
    for name in files:
        command += ["-f", name]
    command += ["config", "--format", "json"]
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


FILE_SETS = [("compose.yaml",), ("compose.yaml", "compose.override.yaml")]


@docker_only
@pytest.mark.parametrize("files", FILE_SETS, ids=["production", "with-override"])
def test_compose_config_is_valid_and_video_worker_defaults_to_four_cpus(files):
    """Criterion 8."""
    done = compose_config(*files)
    assert done.returncode == 0, done.stderr
    worker = json.loads(done.stdout)["services"]["video-worker"]
    assert float(worker["cpus"]) == 4


@docker_only
@pytest.mark.parametrize("files", FILE_SETS, ids=["production", "with-override"])
def test_compose_config_video_worker_cpus_follows_video_cpus_setting(files):
    """Criterion 8."""
    done = compose_config(*files, extra_env={"MAI_VIDEO_CPUS": "2"})
    assert done.returncode == 0, done.stderr
    worker = json.loads(done.stdout)["services"]["video-worker"]
    assert float(worker["cpus"]) == 2


@docker_only
@pytest.mark.parametrize("files", FILE_SETS, ids=["production", "with-override"])
def test_compose_config_video_worker_leaves_unset_tuning_to_the_image(files):
    done = compose_config(*files)
    assert done.returncode == 0, done.stderr
    env = json.loads(done.stdout)["services"]["video-worker"].get("environment") or {}
    assert all(value is None for value in env.values()), env


@docker_only
def test_compose_config_video_worker_receives_a_set_json_override():
    value = '{"brow_down": 0.25}'
    done = compose_config("compose.yaml", extra_env={"MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE": value})
    assert done.returncode == 0, done.stderr
    env = json.loads(done.stdout)["services"]["video-worker"]["environment"]
    assert env["MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE"] == value


@docker_only
def test_compose_config_with_loadtest_overlay_is_valid():
    done = compose_config("compose.yaml", "compose.loadtest.yaml")
    assert done.returncode == 0, done.stderr
    ports = json.loads(done.stdout)["services"]["video-worker"]["ports"]
    assert [(p["host_ip"], str(p["published"]), p["target"]) for p in ports] == [
        ("127.0.0.1", "8002", 8002)
    ]
