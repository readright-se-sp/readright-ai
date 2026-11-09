"""Shared helpers for tests that inspect the packaging files at the repo root."""

import functools
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(read_text(path))


def compose_services(name: str = "compose.yaml") -> dict[str, dict[str, Any]]:
    return load_yaml(ROOT / name)["services"]


def as_text(value: Any) -> str:
    """A command or healthcheck test written as a list or a string, as one string."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return str(value)


def env_of(service: dict[str, Any]) -> dict[str, str]:
    """`environment` written as a mapping or as a KEY=value list."""
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {key: "" if value is None else str(value) for key, value in env.items()}
    result: dict[str, str] = {}
    for item in env:
        key, _, value = str(item).partition("=")
        result[key] = value
    return result


def depends_on_of(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`depends_on` written as a list or as a mapping, always as a mapping."""
    deps = service.get("depends_on") or {}
    if isinstance(deps, dict):
        return {name: dict(cfg or {}) for name, cfg in deps.items()}
    return {name: {} for name in deps}


def volumes_of(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Volume mounts as dicts with source, target and read_only."""
    mounts: list[dict[str, Any]] = []
    for item in service.get("volumes") or []:
        if isinstance(item, dict):
            mounts.append(
                {
                    "source": item.get("source"),
                    "target": item.get("target"),
                    "read_only": bool(item.get("read_only", False)),
                }
            )
            continue
        parts = str(item).split(":")
        options = parts[2].split(",") if len(parts) > 2 else []
        mounts.append(
            {
                "source": parts[0],
                "target": parts[1] if len(parts) > 1 else None,
                "read_only": "ro" in options,
            }
        )
    return mounts


def ports_of(service: dict[str, Any]) -> set[tuple[str, str]]:
    """Published ports as (container port, protocol) pairs."""
    result: set[tuple[str, str]] = set()
    for item in service.get("ports") or []:
        if isinstance(item, dict):
            result.add((str(item["target"]), str(item.get("protocol", "tcp"))))
            continue
        text = str(item)
        proto = "udp" if text.endswith("/udp") else "tcp"
        result.add((text.split("/")[0].split(":")[-1], proto))
    return result


def published_host_ports(service: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in service.get("ports") or []:
        if isinstance(item, dict):
            result.add(str(item.get("published")))
            continue
        parts = str(item).split("/")[0].split(":")
        result.add(parts[-2] if len(parts) >= 2 else parts[0])
    return result


def build_context(service: dict[str, Any]) -> str | None:
    build = service.get("build")
    if isinstance(build, dict):
        return str(build.get("context"))
    return None if build is None else str(build)


def dockerfile_instructions() -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) pairs: comments dropped, continuation lines joined."""
    instructions: list[tuple[str, str]] = []
    pending = ""
    for raw in read_text(SERVER / "Dockerfile").splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        full = (pending + line).strip()
        pending = ""
        word, _, rest = full.partition(" ")
        instructions.append((word.upper(), rest.strip()))
    return instructions


def dockerfile_args(instruction: str) -> list[str]:
    return [args for word, args in dockerfile_instructions() if word == instruction]


def exec_or_shell_words(args: str) -> list[str]:
    """CMD/ENTRYPOINT/HEALTHCHECK arguments in exec (JSON) or shell form, as words."""
    if args.startswith("["):
        return [str(part) for part in json.loads(args)]
    return shlex.split(args)


def dockerfile_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for args in dockerfile_args("ENV"):
        words = shlex.split(args)
        if "=" in words[0]:
            for word in words:
                key, _, value = word.partition("=")
                result[key] = value
        else:
            result[words[0]] = " ".join(words[1:])
    return result


@functools.cache
def docker_ready() -> bool:
    """True when the docker CLI exists and the daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        done = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0
