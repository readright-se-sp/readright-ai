import io
import urllib.request
from pathlib import Path

import pytest
from landmark_helpers import FACE, HAND, REAL_MODEL_DIR, require_models, write_lock

from app.landmarks import ModelError
from devtools.fetch_models import fetch_models, main

PAYLOADS = {FACE: b"face-model-bytes", HAND: b"hand-model-bytes"}


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return write_lock(tmp_path / "models.lock", PAYLOADS)


class Downloader:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.urls: list[str] = []

    def __call__(self, url: str, dest: Path) -> None:
        self.urls.append(url)
        dest.write_bytes(self.payloads[url.rsplit("/", 1)[1]])


def test_downloads_every_lock_entry_and_verifies_hashes(tmp_path, lock):
    """Criterion 1."""
    model_dir = tmp_path / "nested" / "models"
    downloader = Downloader(PAYLOADS)
    fetch_models(model_dir, lock, download=downloader)
    for name, data in PAYLOADS.items():
        assert (model_dir / name).read_bytes() == data
    assert len(downloader.urls) == 2
    assert not list(model_dir.glob("*.part"))


def test_skips_download_of_existing_correct_file(tmp_path, lock):
    """Criterion 1."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / FACE).write_bytes(PAYLOADS[FACE])
    downloader = Downloader(PAYLOADS)
    fetch_models(model_dir, lock, download=downloader)
    assert [url.rsplit("/", 1)[1] for url in downloader.urls] == [HAND]
    assert (model_dir / HAND).read_bytes() == PAYLOADS[HAND]


def test_redownloads_existing_file_with_wrong_hash(tmp_path, lock):
    """Criterion 1."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / FACE).write_bytes(b"corrupt")
    downloader = Downloader(PAYLOADS)
    fetch_models(model_dir, lock, download=downloader)
    assert (model_dir / FACE).read_bytes() == PAYLOADS[FACE]
    assert len(downloader.urls) == 2


def test_rejects_download_with_wrong_hash_and_leaves_no_file(tmp_path, lock):
    """Criterion 1."""
    model_dir = tmp_path / "models"
    downloader = Downloader({FACE: PAYLOADS[FACE], HAND: b"tampered"})
    with pytest.raises(ModelError, match=HAND):
        fetch_models(model_dir, lock, download=downloader)
    assert not (model_dir / HAND).exists()
    assert not (model_dir / f"{HAND}.part").exists()


class FakeResponse(io.BytesIO):
    status = 200
    headers: dict = {}

    def info(self):
        return {}

    def getcode(self):
        return 200


def test_main_returns_nonzero_and_leaves_no_file_on_hash_mismatch(tmp_path, monkeypatch):
    """Criterion 1."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"not a model"))
    model_dir = tmp_path / "models"
    assert main(["--model-dir", str(model_dir)]) != 0
    leftovers = [p.name for p in model_dir.iterdir()] if model_dir.exists() else []
    assert leftovers == []


def test_main_returns_zero_without_downloading_when_models_are_present(monkeypatch):
    """Criterion 1."""
    require_models(use_pose=True)

    def no_network(*args, **kwargs):
        raise AssertionError("must not download")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert main(["--model-dir", str(REAL_MODEL_DIR)]) == 0


def test_wrong_existing_file_and_wrong_download_leave_no_file(tmp_path, lock):
    """Criterion 1."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / FACE).write_bytes(b"corrupt")
    downloader = Downloader({FACE: b"also wrong", HAND: PAYLOADS[HAND]})
    with pytest.raises(ModelError, match=FACE):
        fetch_models(model_dir, lock, download=downloader)
    assert not (model_dir / FACE).exists()
    assert not (model_dir / f"{FACE}.part").exists()
