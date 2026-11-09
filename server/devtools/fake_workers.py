"""Fake audio and video lane workers implementing contracts/worker-api.md, for local runs.

    uv run uvicorn devtools.fake_workers:audio_app --port 8001
    uv run uvicorn devtools.fake_workers:video_app --port 8002

Behavior switches are read from the environment on every request:
FAKE_AUDIO_BEHAVIOR=ok|no_speech|error|slow, FAKE_VIDEO_BEHAVIOR=ok|error|slow,
FAKE_SLOW_SECONDS (default 200).
"""

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.workers import AudioLaneResult, LaneMiscue, VideoLaneResult

# Words may contain inner apostrophes ("Pam's"); surrounding punctuation and quotes are dropped.
_WORD = re.compile(r"\w+(?:['’]\w+)*")


class AudioJob(BaseModel):
    job_id: str
    deadline: int
    wav_path: str
    passage_title: str
    passage_content: str


class VideoJob(BaseModel):
    job_id: str
    deadline: int
    webm_path: str


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status)


def _precheck(deadline: int, path: str) -> JSONResponse | None:
    if deadline <= int(time.time() * 1000):
        return _error(504, "DEADLINE_EXCEEDED", "The job deadline has passed.")
    if not Path(path).is_file():
        return _error(422, "MEDIA_UNREADABLE", "The media file does not exist.")
    return None


async def _slow() -> None:
    await asyncio.sleep(float(os.environ.get("FAKE_SLOW_SECONDS", "200")))


def _build_app(title: str) -> FastAPI:
    app = FastAPI(title=title)

    async def invalid_job(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        return _error(400, "INVALID_JOB", "The job body is invalid.")

    async def http_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        return _error(exc.status_code, "INVALID_JOB", str(exc.detail))

    app.add_exception_handler(RequestValidationError, invalid_job)
    app.add_exception_handler(StarletteHTTPException, http_error)

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {"queue_length": 0, "avg_processing_seconds": None}

    return app


def _audio_result(job: AudioJob) -> AudioLaneResult:
    """Deterministic: first word substituted, third word omitted."""
    words = _WORD.findall(job.passage_content)
    miscues: list[LaneMiscue] = []
    if words:
        actual = words[0]
        spoken = actual[:-1] if len(actual) > 1 else "uh"
        miscues.append(
            LaneMiscue(
                actual_phrase=actual,
                spoken_phrase=spoken,
                actual_phoneme=list(actual),
                spoken_phoneme=list(spoken),
                start_ms=500,
                end_ms=1000,
                miscue_type="substitution",
            )
        )
    if len(words) >= 3:
        miscues.append(
            LaneMiscue(
                actual_phrase=words[2],
                spoken_phrase="",
                actual_phoneme=list(words[2]),
                spoken_phoneme=[],
                start_ms=2000,
                end_ms=2400,
                miscue_type="omission",
            )
        )
    return AudioLaneResult(
        job_id=job.job_id,
        reading_score=85.0,
        wpm=60.0,
        is_word_by_word=False,
        is_disregard_punctuation=True,
        is_monotonous=False,
        is_hardly_audible=False,
        miscues=miscues,
    )


audio_app = _build_app("Fake audio lane")
video_app = _build_app("Fake video lane")


@audio_app.post("/v1/jobs")
async def audio_job(job: AudioJob) -> Any:
    if (rejected := _precheck(job.deadline, job.wav_path)) is not None:
        return rejected
    behavior = os.environ.get("FAKE_AUDIO_BEHAVIOR", "ok")
    if behavior == "no_speech":
        return _error(422, "NO_SPEECH", "No speech was detected.")
    if behavior == "error":
        return _error(500, "INTERNAL", "Simulated audio lane failure.")
    if behavior == "slow":
        await _slow()
    return _audio_result(job)


@video_app.post("/v1/jobs")
async def video_job(job: VideoJob) -> Any:
    if (rejected := _precheck(job.deadline, job.webm_path)) is not None:
        return rejected
    behavior = os.environ.get("FAKE_VIDEO_BEHAVIOR", "ok")
    if behavior == "error":
        return _error(500, "INTERNAL", "Simulated video lane failure.")
    if behavior == "slow":
        await _slow()
    return VideoLaneResult(job_id=job.job_id, is_finger_pointing=False, is_lack_expression=False)
