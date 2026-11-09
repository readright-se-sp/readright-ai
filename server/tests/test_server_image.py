import re
import subprocess

import pytest
from packaging_helpers import (
    SERVER,
    docker_ready,
    dockerfile_args,
    dockerfile_env,
    exec_or_shell_words,
    read_text,
)


def test_image_is_based_on_python_slim_bookworm():
    """Criterion 1."""
    assert "python:3.13-slim-bookworm" in [a.split()[0] for a in dockerfile_args("FROM")]


def test_ffmpeg_is_installed_lean_through_apt():
    """Criterion 1."""
    runs = [a for a in dockerfile_args("RUN") if "ffmpeg" in a]
    assert runs, "no RUN installs ffmpeg"
    run = runs[0]
    assert "apt-get install" in run or "apt install" in run
    assert "--no-install-recommends" in run
    assert "/var/lib/apt/lists" in run and "rm" in run


def test_uv_comes_from_a_pinned_image():
    """Criterion 1/2 (decision D1)."""
    sources = " ".join(dockerfile_args("COPY") + dockerfile_args("FROM"))
    assert re.search(r"ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+", sources)


def test_dependencies_install_from_lockfile_without_dev_extra():
    """Criterion 2."""
    syncs = [a for a in dockerfile_args("RUN") if "uv sync" in a]
    assert syncs, "no RUN uv sync"
    for sync in syncs:
        assert "--frozen" in sync
        assert "--no-dev" in sync
        assert "--extra dev" not in sync and "--all-extras" not in sync


def test_app_and_devtools_are_copied_and_tests_are_not():
    """Criterion 2."""
    copies = " ".join(a for a in dockerfile_args("COPY") if "--from" not in a)
    assert re.search(r"\bapp\b", copies)
    assert re.search(r"\bdevtools\b", copies)
    assert "tests" not in copies


def test_container_runs_as_a_non_root_user():
    """Criterion 3."""
    users = dockerfile_args("USER")
    assert users, "no USER instruction"
    final = users[-1].split(":")[0].strip()
    assert final not in {"root", "0"}


def test_scratch_dir_exists_and_belongs_to_the_runtime_user():
    """Criterion 3."""
    commands = " ".join(dockerfile_args("RUN"))
    assert "/scratch" in commands
    assert re.search(r"chown[^;&|]*/scratch|install[^;&|]*-o[^;&|]*/scratch", commands)
    assert dockerfile_env().get("MAI_SCRATCH_DIR") == "/scratch"


def test_default_command_runs_one_uvicorn_worker_on_port_8000():
    """Criterion 4."""
    cmds = dockerfile_args("CMD")
    assert cmds, "no CMD"
    words = exec_or_shell_words(cmds[-1])
    text = " ".join(words)
    assert "uvicorn" in words
    assert "app.main:create_app" in words
    assert "--factory" in words
    assert re.search(r"--host[ =]0\.0\.0\.0", text)
    assert re.search(r"--port[ =]8000\b", text)
    assert "--reload" not in text
    workers = re.search(r"--workers[ =](\d+)", text)
    assert workers is None or workers.group(1) == "1"


def test_port_8000_is_exposed():
    """Criterion 4."""
    assert any(re.search(r"\b8000\b", a) for a in dockerfile_args("EXPOSE"))


def test_healthcheck_probes_health_live_with_python_not_curl():
    """Criterion 5."""
    checks = dockerfile_args("HEALTHCHECK")
    assert checks, "no HEALTHCHECK"
    text = checks[-1]
    assert "http://127.0.0.1:8000/health/live" in text
    assert "urllib" in text
    assert "curl" not in text and "wget" not in text


def test_dockerignore_excludes_secrets_caches_and_tests():
    """Criterion 6."""
    lines = {
        line.strip().rstrip("/").lstrip("/")
        for line in read_text(SERVER / ".dockerignore").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for name in (".env", ".venv", "tests", "__pycache__", ".pytest_cache", ".ruff_cache"):
        assert any(entry in {name, f"**/{name}"} for entry in lines), f"{name} not ignored"


@pytest.mark.skipif(not docker_ready(), reason="docker daemon not available")
def test_server_image_builds():
    """Criterion 7."""
    done = subprocess.run(
        ["docker", "build", str(SERVER)], capture_output=True, text=True, timeout=1200
    )
    assert done.returncode == 0, done.stderr[-2000:]
