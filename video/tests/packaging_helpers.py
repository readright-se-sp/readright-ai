"""Helpers for tests that inspect the video lane's packaging files and the compose stack."""

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VIDEO_DIR = Path(__file__).resolve().parents[1]
ROOT = VIDEO_DIR.parent
DOCKERFILE = VIDEO_DIR / "Dockerfile"
DOCKERIGNORE = VIDEO_DIR / ".dockerignore"


@dataclass(frozen=True)
class Instruction:
    keyword: str
    raw: str  # everything after the keyword, continuations joined

    @property
    def flags(self) -> list[str]:
        """Leading `--name=value` options (e.g. `--from=...`, `--interval=...`)."""
        flags = []
        for token in self.raw.split():
            if not token.startswith("--"):
                break
            flags.append(token)
        return flags

    @property
    def body(self) -> str:
        """The text after the leading `--flags`."""
        rest = self.raw
        for flag in self.flags:
            rest = rest.split(flag, 1)[1]
        return rest.strip()

    @property
    def exec_form(self) -> list[str] | None:
        """The JSON array when the body is written in exec form, else None."""
        body = self.body
        if body.startswith("["):
            try:
                value = json.loads(body)
            except ValueError:
                return None
            if isinstance(value, list):
                return [str(part) for part in value]
        return None

    @property
    def argv(self) -> list[str]:
        """The command as an argument list, whichever form it was written in."""
        form = self.exec_form
        return form if form is not None else shlex.split(self.body)

    def env_pairs(self) -> dict[str, str]:
        """For ENV: both `KEY=value ...` and legacy `KEY value` spellings."""
        tokens = shlex.split(self.raw)
        if tokens and "=" not in tokens[0]:
            return {tokens[0]: " ".join(tokens[1:])}
        return dict(token.split("=", 1) for token in tokens if "=" in token)


def parse_dockerfile(text: str) -> list[Instruction]:
    instructions: list[Instruction] = []
    pending = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or (not stripped and not pending):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
            continue
        pending += stripped
        keyword, _, raw = pending.partition(" ")
        instructions.append(Instruction(keyword.upper(), raw.strip()))
        pending = ""
    if pending.strip():
        keyword, _, raw = pending.strip().partition(" ")
        instructions.append(Instruction(keyword.upper(), raw.strip()))
    return instructions


def load_dockerfile() -> list[Instruction]:
    return parse_dockerfile(DOCKERFILE.read_text(encoding="utf-8"))


def dockerignore_patterns() -> list[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return str(value)


def docker_ready() -> bool:
    """True when the docker CLI exists and the daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        done = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0
