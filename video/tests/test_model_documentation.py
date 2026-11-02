from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_documents_fetching_models_and_the_benchmark():
    """Criterion 12."""
    assert "devtools.fetch_models" in README
    assert "devtools.benchmark_frames" in README


def test_env_example_lists_the_landmark_settings():
    """Criterion 12."""
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("MAI_VIDEO_MODEL_DIR", "MAI_VIDEO_NUM_HANDS", "MAI_VIDEO_USE_POSE"):
        assert name in env_example
        assert f"`{name}`" in README


def test_gitignore_excludes_downloaded_models():
    """Criterion 12."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "models/" in [line.strip() for line in lines]
