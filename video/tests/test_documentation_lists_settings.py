from pathlib import Path

from app.config import Settings

VIDEO_ROOT = Path(__file__).resolve().parent.parent


def env_names() -> list[str]:
    return [f"MAI_VIDEO_{name.upper()}" for name in Settings.model_fields]


def test_project_documents_exist():
    """Criterion 16."""
    for name in ("README.md", "CLAUDE.md", ".env.example"):
        assert (VIDEO_ROOT / name).is_file(), name


def test_every_setting_is_in_env_example():
    """Criterion 16."""
    text = (VIDEO_ROOT / ".env.example").read_text(encoding="utf-8")
    missing = [name for name in env_names() if name not in text]
    assert not missing


def test_every_setting_is_in_readme():
    """Criterion 16."""
    text = (VIDEO_ROOT / "README.md").read_text(encoding="utf-8")
    missing = [name for name in env_names() if f"`{name}`" not in text]
    assert not missing
