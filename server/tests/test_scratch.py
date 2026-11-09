"""Phase 3: per-request scratch directory (criterion 7)."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from conftest import API_KEY

from app.config import Settings
from app.scratch import scratch_dir, scratch_root

RID = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"


@pytest.fixture
def scratch_settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, api_key=API_KEY, scratch_dir=tmp_path / "root"
    )


def test_c7_scratch_root_uses_setting(scratch_settings, tmp_path):
    assert scratch_root(scratch_settings) == tmp_path / "root"


def test_c7_scratch_root_defaults_to_tempdir(settings):
    assert settings.scratch_dir is None
    assert scratch_root(settings) == Path(tempfile.gettempdir()) / "readright-mai"


def test_c7_yields_request_dir_creating_parents(scratch_settings, tmp_path):
    root = tmp_path / "root"
    assert not root.exists()
    with scratch_dir(scratch_settings, RID) as path:
        assert path == root / RID
        assert path.is_dir()
        (path / "input.webm").write_bytes(b"x")
    assert not path.exists()


def test_c7_clears_leftover_dir_of_same_name(scratch_settings, tmp_path):
    leftover = tmp_path / "root" / RID
    leftover.mkdir(parents=True)
    (leftover / "stale.bin").write_bytes(b"old")
    with scratch_dir(scratch_settings, RID) as path:
        assert path == leftover
        assert list(path.iterdir()) == []
    assert not leftover.exists()


def test_c7_removed_after_error(scratch_settings):
    with pytest.raises(RuntimeError):
        with scratch_dir(scratch_settings, RID) as path:
            (path / "f").write_bytes(b"x")
            raise RuntimeError("boom")
    assert not path.exists()


@pytest.mark.anyio
async def test_c7_removed_after_cancellation(scratch_settings):
    seen: list[Path] = []

    async def work():
        with scratch_dir(scratch_settings, RID) as path:
            seen.append(path)
            (path / "f").write_bytes(b"x")
            await asyncio.sleep(10)

    task = asyncio.create_task(work())
    await asyncio.sleep(0.05)
    assert seen and seen[0].exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not seen[0].exists()


@pytest.mark.anyio
async def test_c7_removed_after_timeout(scratch_settings):
    seen: list[Path] = []
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            with scratch_dir(scratch_settings, RID) as path:
                seen.append(path)
                await asyncio.sleep(10)
    assert not seen[0].exists()
