import pytest
from packaging_helpers import (
    ROOT,
    as_text,
    build_context,
    compose_services,
    depends_on_of,
    env_of,
    load_yaml,
    ports_of,
    published_host_ports,
    volumes_of,
)

LONG_RUNNING = ["caddy", "api", "redis", "audio-worker", "video-worker"]


@pytest.fixture
def services():
    return compose_services("compose.yaml")


@pytest.fixture
def override():
    return compose_services("compose.override.yaml")


def test_production_compose_defines_the_five_services(services):
    """Criterion 8."""
    assert set(services) == set(LONG_RUNNING)


def test_only_caddy_publishes_ports(services):
    """Criterion 8."""
    assert ports_of(services["caddy"]) == {("80", "tcp"), ("443", "tcp"), ("443", "udp")}
    for name in ("api", "redis", "audio-worker", "video-worker"):
        assert not services[name].get("ports"), f"{name} publishes ports"


def test_redis_persists_with_append_only_file_and_checks_health(services):
    """Criterion 9."""
    redis = services["redis"]
    assert redis["image"].startswith("redis:7")
    assert "--appendonly yes" in as_text(redis.get("command"))
    mounts = [m for m in volumes_of(redis) if m["target"] == "/data"]
    assert mounts and mounts[0]["source"] == "redis-data"
    test = as_text(redis["healthcheck"]["test"])
    assert "redis-cli" in test and "ping" in test


def test_scratch_volume_is_writable_in_api_and_read_only_in_workers(services):
    """Criterion 10."""
    api_mounts = [m for m in volumes_of(services["api"]) if m["target"] == "/scratch"]
    assert api_mounts and api_mounts[0]["source"] == "scratch"
    assert not api_mounts[0]["read_only"]
    for name in ("audio-worker", "video-worker"):
        mounts = [m for m in volumes_of(services[name]) if m["target"] == "/scratch"]
        assert mounts and mounts[0]["source"] == "scratch", name
        assert mounts[0]["read_only"], name


def test_named_volumes_are_declared(services):
    """Criteria 9, 10, 14."""
    declared = load_yaml(ROOT / "compose.yaml")["volumes"]
    assert {"scratch", "redis-data", "caddy_data", "caddy_config"} <= set(declared)


def test_api_points_at_scratch_redis_and_workers(services):
    """Criterion 10."""
    env = env_of(services["api"])
    assert env["MAI_SCRATCH_DIR"] == "/scratch"
    assert env["MAI_REDIS_URL"] == "redis://redis:6379/0"
    assert env["MAI_AUDIO_WORKER_URL"] == "http://audio-worker:8001"
    assert env["MAI_VIDEO_WORKER_URL"] == "http://video-worker:8002"


def test_api_key_is_a_required_interpolation_and_not_read_from_server_env_file(services):
    """Criterion 11."""
    api = services["api"]
    assert env_of(api)["MAI_API_KEY"].startswith("${MAI_API_KEY:?")
    env_files = api.get("env_file") or []
    if isinstance(env_files, (str, dict)):
        env_files = [env_files]
    for entry in env_files:
        path = entry.get("path", "") if isinstance(entry, dict) else str(entry)
        assert "server" not in path.replace("\\", "/").split("/")


@pytest.mark.parametrize(
    ("name", "app", "port"),
    [("audio-worker", "audio_app", "8001"), ("video-worker", "video_app", "8002")],
)
def test_fake_workers_run_from_the_api_image(services, name, app, port):
    """Criterion 12."""
    worker, api = services[name], services["api"]
    same_image = worker.get("image") is not None and worker.get("image") == api.get("image")
    same_build = build_context(worker) is not None and build_context(worker) == build_context(api)
    assert same_image or same_build
    command = as_text(worker["command"])
    assert f"uvicorn devtools.fake_workers:{app}" in command
    assert "--host 0.0.0.0" in command
    assert f"--port {port}" in command
    assert "/health/ready" in as_text(worker["healthcheck"]["test"])


def test_service_start_order(services):
    """Criterion 12."""
    api_deps = depends_on_of(services["api"])
    assert api_deps["redis"].get("condition") == "service_healthy"
    assert {"audio-worker", "video-worker"} <= set(api_deps)
    assert "api" in depends_on_of(services["caddy"])


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_long_running_services_restart_unless_stopped(services, name):
    """Criterion 12."""
    assert services[name].get("restart") == "unless-stopped"


def test_override_adds_a_pinned_moto_s3_published_on_5000(override):
    """Criterion 13."""
    s3 = override["s3"]
    image = s3["image"]
    assert image.startswith("motoserver/moto:")
    assert not image.endswith(":latest")
    assert ("5000", "tcp") in ports_of(s3)
    assert "5000" in published_host_ports(s3)


def test_override_s3_init_creates_the_bucket_with_the_api_image(override, services):
    """Criterion 13."""
    init, api = override["s3-init"], services["api"]
    same_image = init.get("image") is not None and init.get("image") == api.get("image")
    same_build = build_context(init) is not None and build_context(init) == build_context(api)
    assert same_image or same_build
    command = as_text(init["command"])
    assert "boto3" in command
    assert "readright-local" in command
    assert "http://s3:5000" in command
    assert init.get("restart", "no") in {"no", False}


def test_override_gives_api_local_s3_settings_and_dummy_credentials(override):
    """Criterion 13."""
    api = override["api"]
    env = env_of(api)
    assert env["MAI_S3_BUCKET"] == "readright-local"
    assert env["MAI_S3_ENDPOINT_URL"] == "http://s3:5000"
    assert "AWS_REGION" in env
    assert env["AWS_ACCESS_KEY_ID"] == "testing"
    assert env["AWS_SECRET_ACCESS_KEY"] == "testing"
    assert depends_on_of(api)["s3-init"].get("condition") == "service_completed_successfully"


def test_production_api_takes_bucket_and_region_from_env_without_static_credentials(services):
    """Criterion 13."""
    env = env_of(services["api"])
    assert "${MAI_S3_BUCKET" in env["MAI_S3_BUCKET"]
    assert "${AWS_REGION" in env["AWS_REGION"]
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "MAI_S3_ENDPOINT_URL" not in env
