"""Feature extraction isolates per-file failures and rejects empty inputs."""

from contextlib import contextmanager
from pathlib import Path

import pytest
from devtool_helpers import FakeSession, make_webm, patch_video_io
from pointing_helpers import make_features

from app.frames import Frame, SamplePlan
from app.landmarks import FrameFeatures
from devtools import extract_features
from devtools.feature_cache import save_cache

PLAN = SamplePlan(fps=5.0, width=64, height=48, max_frames=900)


class ExplodingSession:
    def process(self, frame: Frame) -> FrameFeatures:
        raise RuntimeError("landmarker blew up")


def factory_failing_on_first_file():
    """The first file processed gets a session that raises; later files get a working one."""
    calls = {"count": 0}

    @contextmanager
    def factory():
        calls["count"] += 1
        yield ExplodingSession() if calls["count"] == 1 else FakeSession()

    return factory


@pytest.fixture
def webm_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    patch_video_io(monkeypatch, extract_features)
    make_webm(tmp_path / "webms", "a.webm")
    make_webm(tmp_path / "webms", "b.webm")
    return tmp_path / "webms"


def run(args: list[str], capsys: pytest.CaptureFixture[str], factory=None) -> tuple[int, str]:
    code = extract_features.main(args, session_factory=factory)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_prints_a_failing_file_skips_it_and_still_processes_the_rest(
    webm_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 14."""
    cache = tmp_path / "cache"
    code, out = run([str(webm_dir), "--cache", str(cache)], capsys, factory_failing_on_first_file())
    assert code == 1
    assert "a.webm" in out
    assert "landmarker blew up" in out
    assert sorted(p.name for p in cache.iterdir()) == ["b.npz"]


def test_deletes_a_stale_cache_file_when_its_webm_fails(
    webm_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 14."""
    cache = tmp_path / "cache"
    cache.mkdir()
    save_cache(cache / "a.npz", make_features(3), PLAN)
    code, _ = run([str(webm_dir), "--cache", str(cache)], capsys, factory_failing_on_first_file())
    assert code == 1
    assert not (cache / "a.npz").exists()
    assert (cache / "b.npz").exists()


def test_exits_1_with_a_message_when_the_webm_dir_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 14."""
    code, out = run([str(tmp_path / "nope"), "--cache", str(tmp_path / "cache")], capsys)
    assert code == 1
    assert out.strip() != ""


def test_exits_1_with_a_message_when_the_dir_has_no_webm_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 14."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("x")
    code, out = run([str(empty), "--cache", str(tmp_path / "cache")], capsys)
    assert code == 1
    assert out.strip() != ""
