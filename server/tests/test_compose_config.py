import json
import os
import subprocess

import pytest
from packaging_helpers import ROOT, docker_ready

pytestmark = pytest.mark.skipif(not docker_ready(), reason="docker daemon not available")


def compose_config(*files: str, api_key: str | None = "dummy-not-a-real-key"):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MAI_")}
    env.pop("AWS_ACCESS_KEY_ID", None)
    env.pop("AWS_SECRET_ACCESS_KEY", None)
    if api_key is not None:
        env["MAI_API_KEY"] = api_key
    command = ["docker", "compose"]
    for name in files:
        command += ["-f", name]
    command += ["config", "--format", "json"]
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_config_with_override_is_valid_and_includes_local_s3():
    """Criterion 8, 13."""
    done = compose_config("compose.yaml", "compose.override.yaml")
    assert done.returncode == 0, done.stderr
    services = json.loads(done.stdout)["services"]
    assert {"caddy", "api", "redis", "audio-worker", "video-worker", "s3", "s3-init"} <= set(
        services
    )
    assert services["api"]["environment"]["AWS_ACCESS_KEY_ID"] == "testing"
    assert "minio" not in done.stdout.lower()


def test_config_without_override_is_valid_and_has_only_production_services():
    """Criterion 8, 13."""
    done = compose_config("compose.yaml")
    assert done.returncode == 0, done.stderr
    services = json.loads(done.stdout)["services"]
    assert set(services) == {"caddy", "api", "redis", "audio-worker", "video-worker"}
    assert "AWS_ACCESS_KEY_ID" not in services["api"]["environment"]


def test_config_fails_when_the_api_key_is_missing():
    """Criterion 11."""
    done = compose_config("compose.yaml", api_key=None)
    assert done.returncode != 0
    assert "MAI_API_KEY" in done.stderr
