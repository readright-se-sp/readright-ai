import json
import os
import re
import subprocess

import pytest
from packaging_helpers import ROOT, as_text, docker_ready, load_yaml


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
