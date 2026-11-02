import json
import logging
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .analyzer import JobContext
from .config import Settings
from .errors import LaneError
from .models import ErrorCode

logger = logging.getLogger(__name__)

_UNREADABLE = "The recording could not be read."
_REAP_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    rotation: int
    duration_ms: int | None


@dataclass(frozen=True)
class SamplePlan:
    fps: float
    width: int
    height: int
    max_frames: int


@dataclass(frozen=True)
class Frame:
    index: int
    timestamp_ms: int
    rgb: np.ndarray


def _run_ffprobe(args: list[str], path: Path, settings: Settings) -> dict[str, Any]:
    cmd = [settings.ffprobe_path, "-v", "error", "-select_streams", "v:0", *args, "-of", "json"]
    cmd.append(str(path))
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=settings.probe_timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired:
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, _UNREADABLE) from None
    except OSError:
        raise LaneError(ErrorCode.INTERNAL, "ffprobe is not available.") from None
    if result.returncode != 0:
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, _UNREADABLE)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, _UNREADABLE) from None
    if not isinstance(data, dict):
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, _UNREADABLE)
    return data


def _seconds_to_ms(value: Any) -> int | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds != seconds or seconds < 0 or seconds == float("inf"):
        return None
    return round(seconds * 1000)


def _rotation(stream: dict[str, Any]) -> int:
    raw: Any = None
    for side_data in stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            raw = side_data["rotation"]
            break
    if raw is None:
        tags = stream.get("tags")
        if isinstance(tags, dict):
            raw = tags.get("rotate")
    if raw is None:
        return 0
    try:
        return round(float(raw) / 90) * 90 % 360
    except (TypeError, ValueError, OverflowError):
        return 0


def probe(path: Path, settings: Settings) -> VideoInfo:
    data = _run_ffprobe(
        [
            "-show_entries",
            "stream=width,height,duration:stream_side_data=rotation:stream_tags=rotate"
            ":format=duration",
        ],
        path,
        settings,
    )
    streams = data.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, "The recording has no video stream.")
    stream = streams[0]
    width, height = stream.get("width"), stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, _UNREADABLE)
    rotation = _rotation(stream)
    if rotation in (90, 270):
        width, height = height, width

    duration_ms = _seconds_to_ms(stream.get("duration"))
    if duration_ms is None:
        fmt = data.get("format")
        if isinstance(fmt, dict):
            duration_ms = _seconds_to_ms(fmt.get("duration"))
    if duration_ms is None:
        packets = _run_ffprobe(["-show_entries", "packet=pts_time"], path, settings).get("packets")
        if isinstance(packets, list):
            times = [
                ms
                for p in packets
                if isinstance(p, dict) and (ms := _seconds_to_ms(p.get("pts_time"))) is not None
            ]
            if times:
                duration_ms = max(times)
    return VideoInfo(width=width, height=height, rotation=rotation, duration_ms=duration_ms)


def _even_down(value: int) -> int:
    return value - value % 2


def plan_sampling(info: VideoInfo, settings: Settings) -> SamplePlan:
    fps = settings.sample_fps
    if info.duration_ms is not None and info.duration_ms > 0:
        fps = min(fps, settings.max_frames * 1000 / info.duration_ms)
    width = max(2, _even_down(min(settings.frame_width, info.width)))
    height = max(2, _even_down(round(width * info.height / info.width)))
    return SamplePlan(fps=fps, width=width, height=height, max_frames=settings.max_frames)


def _read_exactly(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def iter_frames(
    path: Path, plan: SamplePlan, ctx: JobContext, settings: Settings
) -> Iterator[Frame]:
    cmd = [
        settings.ffmpeg_path,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-an",
        "-sn",
        "-vf",
        f"fps={plan.fps},scale={plan.width}:{plan.height}",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        raise LaneError(ErrorCode.INTERNAL, "ffmpeg is not available.") from None

    frame_size = plan.width * plan.height * 3
    count = 0
    exit_code: int | None = None
    try:
        assert proc.stdout is not None
        while count < plan.max_frames:
            ctx.check()
            data = _read_exactly(proc.stdout, frame_size)
            if len(data) < frame_size:
                try:
                    exit_code = proc.wait(timeout=_REAP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
                break
            rgb = np.frombuffer(data, dtype=np.uint8).reshape(plan.height, plan.width, 3)
            yield Frame(count, round(count * 1000 / plan.fps), rgb)
            count += 1
    finally:
        proc.kill()
        try:
            proc.wait(timeout=_REAP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg did not exit within %ss after kill", _REAP_TIMEOUT_SECONDS)
    if count == 0:
        raise LaneError(ErrorCode.MEDIA_UNREADABLE, _UNREADABLE)
    if exit_code:
        logger.warning("ffmpeg exited with code %s after %s frames", exit_code, count)
