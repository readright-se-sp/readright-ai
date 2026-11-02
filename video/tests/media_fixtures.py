"""Generate small webm clips with ffmpeg at test time (never committed)."""

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_CACHE: dict[str, Path] = {}
_DIR: Path | None = None


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not found on PATH")


def _dir() -> Path:
    global _DIR
    if _DIR is None:
        _DIR = Path(tempfile.mkdtemp(prefix="video-media-"))
        atexit.register(shutil.rmtree, _DIR, ignore_errors=True)
    return _DIR


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", *args],
        check=True,
        capture_output=True,
    )


def _source(seconds: float, rate: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc=size=320x240:rate={rate}:duration={seconds}"]


def make_clip(codec: str = "vp8", seconds: float = 4, rate: int = 30) -> Path:
    """A 320x240 testsrc clip. codec is 'vp8' or 'vp9'."""
    key = f"{codec}-{seconds}-{rate}"
    if key not in _CACHE:
        require_ffmpeg()
        lib = {"vp8": "libvpx", "vp9": "libvpx-vp9"}[codec]
        out = _dir() / f"{key}.webm"
        _ffmpeg(*_source(seconds, rate), "-c:v", lib, "-pix_fmt", "yuv420p", str(out))
        _CACHE[key] = out
    return _CACHE[key]


def make_rotated_clip(rotation: int = 90) -> Path:
    key = f"rot{rotation}"
    if key not in _CACHE:
        src = make_clip()
        out = _dir() / f"{key}.webm"
        _ffmpeg("-display_rotation", str(rotation), "-i", str(src), "-c", "copy", str(out))
        _CACHE[key] = out
    return _CACHE[key]


def make_durationless_clip(seconds: float = 4, rate: int = 30) -> Path:
    """Written to a pipe so the header has no duration."""
    key = f"nodur-{seconds}-{rate}"
    if key not in _CACHE:
        require_ffmpeg()
        out = _dir() / f"{key}.webm"
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", *_source(seconds, rate)]
        command += ["-c:v", "libvpx", "-pix_fmt", "yuv420p", "-f", "webm", "-"]
        result = subprocess.run(command, check=True, capture_output=True)
        out.write_bytes(result.stdout)
        _CACHE[key] = out
    return _CACHE[key]


def make_audio_only_clip() -> Path:
    key = "audio"
    if key not in _CACHE:
        require_ffmpeg()
        out = _dir() / "audio.webm"
        _ffmpeg("-f", "lavfi", "-i", "sine=duration=2", "-c:a", "libopus", str(out))
        _CACHE[key] = out
    return _CACHE[key]


def make_truncated_clip(fraction: float) -> Path:
    """The vp8 clip cut to a fraction of its bytes."""
    key = f"trunc-{fraction}"
    if key not in _CACHE:
        data = make_clip().read_bytes()
        out = _dir() / f"{key}.webm"
        out.write_bytes(data[: int(len(data) * fraction)])
        _CACHE[key] = out
    return _CACHE[key]


def make_header_only_clip() -> Path:
    key = "header-only"
    if key not in _CACHE:
        data = make_clip().read_bytes()
        out = _dir() / "header-only.webm"
        out.write_bytes(data[:200])
        _CACHE[key] = out
    return _CACHE[key]
