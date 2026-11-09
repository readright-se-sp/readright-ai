import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from video_helpers import future_deadline_ms, make_settings

from app.analyzer import JobContext
from app.errors import LaneError
from app.frames import SamplePlan, VideoInfo, iter_frames, probe
from app.models import ErrorCode


class FakeProcess:
    def __init__(self, data: bytes = b"") -> None:
        self.stdout = io.BytesIO(data)
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_iter_frames_launches_the_exact_ffmpeg_command(monkeypatch: pytest.MonkeyPatch):
    """Criterion 13."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    plan = SamplePlan(fps=5.0, width=4, height=2, max_frames=10)

    def fake_popen(*args: Any, **kwargs: Any) -> FakeProcess:
        calls.append((args, kwargs))
        return FakeProcess(bytes(4 * 2 * 3))

    monkeypatch.setattr("app.frames.subprocess.Popen", fake_popen)
    settings = make_settings(ffmpeg_path="my-ffmpeg")
    ctx = JobContext("j", future_deadline_ms())
    path = Path("some") / "clip.webm"
    frames = list(iter_frames(path, plan, ctx, settings))

    assert len(frames) == 1
    assert len(calls) == 1
    args, kwargs = calls[0]
    cmd = args[0] if args else kwargs["args"]
    assert isinstance(cmd, list)
    assert cmd[:6] == ["my-ffmpeg", "-nostdin", "-v", "error", "-i", str(path)]
    assert cmd[6:8] == ["-an", "-sn"]
    assert cmd[8] == "-vf"
    fps_part, scale_part = cmd[9].split(",")
    assert fps_part.startswith("fps=")
    assert float(fps_part.removeprefix("fps=")) == 5.0
    assert scale_part == "scale=4:2"
    assert cmd[10:] == ["-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def _fake_run(responses: list[dict[str, Any]], calls: list[list[str]]):
    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(cmd)
        payload = responses[len(calls) - 1]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    return fake_run


def test_probe_launches_the_exact_ffprobe_command(monkeypatch: pytest.MonkeyPatch):
    """Criterion 13."""
    calls: list[list[str]] = []
    payload = {"streams": [{"width": 320, "height": 240, "duration": "4.0"}]}
    monkeypatch.setattr("app.frames.subprocess.run", _fake_run([payload], calls))
    settings = make_settings(ffprobe_path="my-ffprobe")
    path = Path("some") / "clip.webm"
    info = probe(path, settings)

    assert info == VideoInfo(width=320, height=240, rotation=0, duration_ms=4000)
    assert calls == [
        [
            "my-ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration:stream_side_data=rotation:stream_tags=rotate"
            ":format=duration",
            "-of",
            "json",
            str(path),
        ]
    ]


def test_probe_falls_back_to_last_packet_pts_time(monkeypatch: pytest.MonkeyPatch):
    """Criterion 13."""
    calls: list[list[str]] = []
    responses = [
        {"streams": [{"width": 320, "height": 240}], "format": {}},
        {"packets": [{"pts_time": "0.000"}, {"pts_time": "3.500"}]},
    ]
    monkeypatch.setattr("app.frames.subprocess.run", _fake_run(responses, calls))
    settings = make_settings(ffprobe_path="my-ffprobe")
    path = Path("clip.webm")
    info = probe(path, settings)

    assert info.duration_ms == 3500
    assert calls[1] == [
        "my-ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time",
        "-of",
        "json",
        str(path),
    ]


def test_probe_passes_timeout_to_every_call(monkeypatch: pytest.MonkeyPatch):
    """Criterion 13."""
    seen: list[Any] = []
    payload = {"streams": [{"width": 320, "height": 240, "duration": "1.0"}]}

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr("app.frames.subprocess.run", fake_run)
    probe(Path("clip.webm"), make_settings(probe_timeout_seconds=3.5))
    assert seen == [3.5]


@pytest.mark.parametrize(
    ("side_data", "tags", "expected_rotation", "expected_size"),
    [
        ([{"rotation": -90}], None, 270, (240, 320)),
        ([{"rotation": 90}], None, 90, (240, 320)),
        (None, {"rotate": "180"}, 180, (320, 240)),
        ([{"rotation": 80}], None, 90, (240, 320)),
    ],
)
def test_probe_normalizes_rotation(
    monkeypatch: pytest.MonkeyPatch, side_data, tags, expected_rotation, expected_size
):
    """Criterion 13."""
    stream: dict[str, Any] = {"width": 320, "height": 240, "duration": "1.0"}
    if side_data is not None:
        stream["side_data_list"] = side_data
    if tags is not None:
        stream["tags"] = tags
    monkeypatch.setattr("app.frames.subprocess.run", _fake_run([{"streams": [stream]}], []))
    info = probe(Path("clip.webm"), make_settings())
    assert info.rotation == expected_rotation
    assert (info.width, info.height) == expected_size


def test_probe_timeout_is_media_unreadable(monkeypatch: pytest.MonkeyPatch):
    """Criterion 13."""

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr("app.frames.subprocess.run", fake_run)
    with pytest.raises(LaneError) as excinfo:
        probe(Path("clip.webm"), make_settings())
    assert excinfo.value.code == ErrorCode.MEDIA_UNREADABLE
