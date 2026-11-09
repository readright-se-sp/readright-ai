import json
import re

import pytest
from packaging_helpers import DOCKERFILE, DOCKERIGNORE, dockerignore_patterns, load_dockerfile


@pytest.fixture
def instructions():
    return load_dockerfile()


def of(instructions, keyword):
    return [i for i in instructions if i.keyword == keyword]


def env_of(instructions):
    env = {}
    for item in of(instructions, "ENV"):
        env.update(item.env_pairs())
    return env


def test_dockerfile_and_dockerignore_exist():
    """Criteria 1, 5."""
    assert DOCKERFILE.is_file()
    assert DOCKERIGNORE.is_file()


def test_image_is_based_on_python_313_slim_bookworm(instructions):
    """Criterion 1."""
    assert of(instructions, "FROM")[0].body.split()[0] == "python:3.13-slim-bookworm"


def test_ffmpeg_is_installed_with_apt_without_recommends_and_lists_removed_in_one_run(
    instructions,
):
    """Criterion 1."""
    runs = [r for r in of(instructions, "RUN") if "ffmpeg" in r.raw and "apt-get" in r.raw]
    assert len(runs) == 1
    raw = runs[0].raw
    assert "install" in raw
    assert "--no-install-recommends" in raw
    assert re.search(r"rm\s+-rf\s+/var/lib/apt/lists", raw)


def test_uv_is_copied_from_a_version_pinned_ghcr_image(instructions):
    """Criterion 1."""
    sources = [
        flag.split("=", 1)[1]
        for copy in of(instructions, "COPY")
        for flag in copy.flags
        if flag.startswith("--from=")
    ]
    uv = [s for s in sources if s.startswith("ghcr.io/astral-sh/uv:")]
    assert uv, sources
    assert re.fullmatch(r"\d+\.\d+\.\d+", uv[0].split(":", 1)[1]), uv[0]


def test_dependencies_are_installed_from_the_frozen_lock_without_dev_group(instructions):
    """Criterion 1."""
    syncs = [r for r in of(instructions, "RUN") if "uv sync" in r.raw]
    assert syncs
    assert "--frozen" in syncs[0].raw
    assert "--no-dev" in syncs[0].raw


def test_tests_are_never_copied_into_the_image(instructions):
    """Criterion 1."""
    copies = of(instructions, "COPY") + of(instructions, "ADD")
    assert copies
    for copy in copies:
        for source in copy.body.split()[:-1]:
            assert source.strip("./").split("/")[0] != "tests", copy.raw
            assert source not in {".", "./"}, f"whole-context copy: {copy.raw}"


def test_models_are_fetched_at_build_time_after_their_inputs_are_copied(instructions):
    """Criterion 2."""
    fetch = next(
        (
            position
            for position, i in enumerate(instructions)
            if i.keyword == "RUN" and "devtools.fetch_models" in i.raw
        ),
        None,
    )
    assert fetch is not None, "no RUN of devtools.fetch_models"
    normalized = instructions[fetch].raw.replace("--model-dir=", "--model-dir ")
    assert "--model-dir /models" in re.sub(r"\s+", " ", normalized)
    for needle in ("models.lock", "app", "fetch_models.py"):
        assert any(i.keyword == "COPY" and needle in i.body for i in instructions[:fetch]), (
            f"{needle} not copied before the fetch"
        )


def test_model_directory_environment_points_at_the_baked_models(instructions):
    """Criterion 2."""
    assert env_of(instructions)["MAI_VIDEO_MODEL_DIR"] == "/models"


def test_container_runs_as_a_non_root_user_created_with_uid_1000(instructions):
    """Criterion 3."""
    users = of(instructions, "USER")
    assert users
    user = users[-1].body.split(":")[0]
    assert user not in {"root", "0"}
    creators = [r for r in of(instructions, "RUN") if "useradd" in r.raw or "adduser" in r.raw]
    assert any(re.search(r"(--uid|-u)[ =]1000\b", r.raw) for r in creators)
    assert any(re.search(rf"\b{re.escape(user)}\b", r.raw) for r in creators)


def test_allowed_media_directory_environment_is_scratch(instructions):
    """Criterion 3."""
    assert env_of(instructions)["MAI_VIDEO_ALLOWED_MEDIA_DIR"] == "/scratch"


def test_image_exposes_port_8002(instructions):
    """Criterion 3."""
    exposed = [port.split("/")[0] for e in of(instructions, "EXPOSE") for port in e.body.split()]
    assert "8002" in exposed


def test_command_runs_uvicorn_factory_on_8002_without_workers_or_reload(instructions):
    """Criterion 3."""
    cmds = of(instructions, "CMD")
    assert len(cmds) == 1
    argv = cmds[0].argv
    assert argv[0] == "uvicorn"
    assert "app.main:create_app" in argv
    assert "--factory" in argv
    assert argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--port") + 1] == "8002"
    assert not any(a.startswith("--workers") for a in argv)
    assert "--reload" not in argv


def test_healthcheck_probes_ready_endpoint_with_python_urllib_and_has_a_start_period(
    instructions,
):
    """Criterion 4."""
    checks = of(instructions, "HEALTHCHECK")
    assert len(checks) == 1
    check = checks[0]
    assert any(f.startswith("--start-period=") for f in check.flags)
    assert "curl" not in check.raw and "wget" not in check.raw
    command = check.body
    assert command.startswith("CMD")
    command = command[3:].strip()
    argv = json.loads(command) if command.startswith("[") else command.split()
    assert argv[0] == "python"
    joined = " ".join(argv)
    assert "urllib" in joined
    assert "http://127.0.0.1:8002/health/ready" in joined


@pytest.mark.parametrize(
    "entry",
    [
        ".env",
        ".venv",
        "tests",
        "models",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".feature-cache",
    ],
)
def test_dockerignore_excludes_local_and_generated_files(entry):
    """Criterion 5."""
    normalized = {p.lstrip("/").removeprefix("**/").rstrip("/") for p in dockerignore_patterns()}
    normalized |= {p.removesuffix("/**") for p in normalized}
    assert entry in normalized


def test_dockerignore_keeps_env_example_when_excluding_env_wildcards():
    """Criterion 5."""
    patterns = dockerignore_patterns()
    if ".env.*" in patterns:
        assert "!.env.example" in patterns
