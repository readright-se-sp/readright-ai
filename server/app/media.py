import asyncio
import json
import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .errors import MAIError
from .models import ErrorCode

logger = logging.getLogger(__name__)

_STDERR_TAIL = 500


async def run_process(args: Sequence[str]) -> tuple[int, bytes, bytes]:
    """Run a child process; kill it if we are cancelled."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:  # Windows selector loop has no subprocess support
        return await _run_with_popen(args)
    try:
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout, stderr


async def _run_with_popen(args: Sequence[str]) -> tuple[int, bytes, bytes]:
    proc = subprocess.Popen(
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.to_thread(proc.communicate)
    except asyncio.CancelledError:
        proc.kill()
        await asyncio.shield(asyncio.to_thread(proc.wait))
        raise
    return proc.returncode, stdout, stderr


@dataclass(frozen=True)
class ProbeResult:
    format_name: str
    has_audio: bool
    has_video: bool
    duration_seconds: float | None


def _unsupported(reason: str) -> MAIError:
    return MAIError(ErrorCode.UNSUPPORTED_MEDIA, f"Unsupported media: {reason}.")


async def probe(path: Path, settings: Settings) -> ProbeResult:
    args = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        returncode, stdout, stderr = await run_process(args)
    except FileNotFoundError:
        logger.error("ffprobe binary not found", extra={"binary": settings.ffprobe_path})
        raise MAIError(ErrorCode.PROCESSING_FAILED, "Media processing is unavailable.") from None
    if returncode != 0:
        logger.warning(
            "ffprobe failed",
            extra={
                "returncode": returncode,
                "stderr_tail": stderr[-_STDERR_TAIL:].decode(errors="replace"),
            },
        )
        raise _unsupported("not a readable media file")
    try:
        data = json.loads(stdout)
        fmt = data.get("format") or {}
        format_name = str(fmt.get("format_name", ""))
        kinds = {s.get("codec_type") for s in data.get("streams") or []}
        raw_duration = fmt.get("duration")
        duration = float(raw_duration) if raw_duration is not None else None
    except (ValueError, TypeError, AttributeError):
        raise _unsupported("not a readable media file") from None
    if not {"webm", "matroska"} & set(format_name.split(",")):
        raise _unsupported("the recording must be a webm file")
    result = ProbeResult(format_name, "audio" in kinds, "video" in kinds, duration)
    if not result.has_audio:
        raise _unsupported("the recording has no audio")
    if not result.has_video:
        raise _unsupported("the recording has no video")
    return result


async def convert_to_wav(src: Path, dest: Path, settings: Settings) -> None:
    args = [
        settings.ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    failure = MAIError(ErrorCode.PROCESSING_FAILED, "Audio extraction failed.")
    try:
        returncode, _, stderr = await run_process(args)
    except FileNotFoundError:
        logger.error("ffmpeg binary not found", extra={"binary": settings.ffmpeg_path})
        raise failure from None
    if returncode != 0 or not dest.exists():
        logger.error(
            "ffmpeg failed",
            extra={
                "returncode": returncode,
                "stderr_tail": stderr[-_STDERR_TAIL:].decode(errors="replace"),
            },
        )
        raise failure
