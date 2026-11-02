import re
import shutil
import subprocess

import pytest
from packaging_helpers import ROOT, SERVER, read_text

CHANGED_FILES = [
    SERVER / "Dockerfile",
    SERVER / ".dockerignore",
    ROOT / "compose.yaml",
    ROOT / "compose.override.yaml",
    ROOT / "Caddyfile",
    ROOT / ".env.example",
    ROOT / ".gitattributes",
    ROOT / "scripts" / "start-stack.sh",
    ROOT / "scripts" / "idle-check.sh",
]

KEY_ASSIGNMENT = re.compile(r"\bMAI_API_KEY\b(?!_)\s*[:=]\s*(\S+)")


def test_no_changed_file_holds_a_literal_api_key():
    """Criterion 11."""
    for path in CHANGED_FILES:
        for match in KEY_ASSIGNMENT.finditer(read_text(path)):
            value = match.group(1).strip("'\"")
            interpolated = value.startswith("$")
            placeholder = value == "change-me" and path.name == ".env.example"
            assert interpolated or placeholder, f"literal key value in {path.name}"


def test_minio_is_not_used_anywhere():
    """Criterion 13."""
    for path in [*CHANGED_FILES, SERVER / ".env.example", SERVER / "README.md"]:
        assert "minio" not in read_text(path).lower(), path.name


def test_root_env_example_lists_settings_and_warns_against_committing_env():
    """Criterion 15."""
    text = read_text(ROOT / ".env.example")
    assert re.search(r"^MAI_API_KEY=change-me\s*$", text, re.MULTILINE)
    for name in ("MAI_SITE_ADDRESS", "MAI_S3_BUCKET", "AWS_REGION", "MAI_LOG_LEVEL"):
        assert re.search(rf"^#?\s*{name}=", text, re.MULTILINE), name
    assert re.search(r"never commit", text, re.IGNORECASE)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_root_env_file_is_git_ignored():
    """Criterion 15."""
    done = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", ".env"], cwd=ROOT, capture_output=True
    )
    assert done.returncode == 0
