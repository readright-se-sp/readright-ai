"""The audio worker HTTP API (contracts/worker-api.md): POST /v1/jobs, /health/ready, /stats."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from audio.alignment.text import tokenize_passage
from audio.config import AudioSettings
from audio.errors import MediaUnreadable, NoSpeech
from audio.models import AudioResult, SpokenWord
from audio.phonetics.g2p import G2P, build_g2p
from audio.recognition.audio_io import load_wav
from audio.transcription.crisper_whisper import TranscriberProtocol
from audio.wiring import (
    build_audio_pipeline,
    build_phonetic_reviewer,
    load_phoneme_recognizer,
    load_transcriber,
)
from audio.worker.jobs import JobCancelled, JobRunner
from audio.worker.logging import configure_logging, job_id_var
from audio.worker.mapping import to_worker_result
from audio.worker.schemas import (
    AudioJob,
    AudioWorkerResult,
    ErrorBody,
    ErrorCode,
    ErrorResponse,
    LaneError,
)

logger = logging.getLogger(__name__)


class PipelineProtocol(Protocol):
    def run(
        self,
        wav_path: str,
        passage_text: str,
        proper_nouns: Sequence[str] = (),
        deadline_ms: int | None = None,
    ) -> AudioResult: ...


@dataclass(frozen=True)
class Engine:
    """What the app needs once the models are loaded."""

    pipeline: PipelineProtocol
    g2p: G2P


PipelineFactory = Callable[[AudioSettings], Engine]


class _NoSpeechGuard:
    """Wraps the transcriber so zero ASR words raise NoSpeech before Lane A runs.

    Keeps the check on the worker side (pipeline.py is untouched) and Lane B on, since the
    pipeline still gets the WAV path.
    """

    def __init__(self, inner: TranscriberProtocol) -> None:
        self._inner = inner

    def transcribe(self, wav_path: str) -> list[SpokenWord]:
        start = time.perf_counter()
        words = self._inner.transcribe(wav_path)
        logger.info("asr done", extra={"asr_ms": round((time.perf_counter() - start) * 1000, 1)})
        if not words:
            raise NoSpeech()
        return words


def default_engine(settings: AudioSettings) -> Engine:
    """Loads the real models (slow: tens of seconds). Runs once, in a thread, at startup."""
    transcriber = load_transcriber(settings)
    recogniser = load_phoneme_recognizer(settings)
    reviewer = build_phonetic_reviewer(recogniser, settings) if recogniser is not None else None
    pipeline = build_audio_pipeline(_NoSpeechGuard(transcriber), reviewer)
    return Engine(pipeline, build_g2p(settings))


def _error_response(status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(body.model_dump(mode="json"), status)


async def _lane_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LaneError)
    return _error_response(exc.status, exc.code, exc.message)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Generic message on purpose: never echo the submitted fields (passage text, paths).
    return _error_response(400, ErrorCode.INVALID_JOB, "Job body is invalid.")


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    response = _error_response(exc.status_code, ErrorCode.INVALID_JOB, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return _error_response(500, ErrorCode.INTERNAL, "Internal error.")


def _check_path(settings: AudioSettings, wav_path: str) -> Path:
    path = Path(wav_path)
    if "\x00" in wav_path or not path.is_absolute():
        raise LaneError(ErrorCode.INVALID_JOB, "wav_path must be an absolute path.")
    try:
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            resolved = path.resolve()  # missing file: the WAV check answers 422
        inside = resolved.is_relative_to(settings.WORKER_ALLOWED_MEDIA_DIR.resolve())
    except (ValueError, OSError, RuntimeError):
        raise LaneError(ErrorCode.INVALID_JOB, "wav_path is invalid.") from None
    if not inside:
        raise LaneError(ErrorCode.INVALID_JOB, "wav_path is outside the media directory.")
    return path


def _phonetic_check_states(result: AudioResult) -> dict[str, int]:
    states: dict[str, int] = {}
    for row in result["words"]:
        evidence = row.get("phonetic")
        if evidence is not None:
            states[evidence["check"]] = states.get(evidence["check"], 0) + 1
    return states


def create_app(
    settings: AudioSettings | None = None, pipeline_factory: PipelineFactory | None = None
) -> FastAPI:
    settings = settings or AudioSettings()
    configure_logging(settings.WORKER_LOG_LEVEL)
    factory = pipeline_factory or default_engine

    async def load_engine(app: FastAPI) -> None:
        start = time.perf_counter()
        try:
            app.state.engine = await asyncio.to_thread(factory, settings)
        except Exception:
            logger.exception("model load failed; the worker stays not ready")
            return
        logger.info(
            "models loaded",
            extra={"load_s": round(time.perf_counter() - start, 1)},
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.engine = None
        app.state.jobs = JobRunner(settings)
        logger.warning(
            "is_word_by_word, is_disregard_punctuation, is_monotonous and is_hardly_audible "
            "are not implemented; they are always false"
        )
        load_task = asyncio.create_task(load_engine(app))
        try:
            yield
        finally:
            load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await load_task

    app = FastAPI(title="ReadRight audio worker", lifespan=lifespan)
    app.add_exception_handler(LaneError, _lane_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)

    @app.post("/v1/jobs")
    async def create_job(job: AudioJob, request: Request) -> AudioWorkerResult:
        token = job_id_var.set(str(job.job_id))
        start = time.perf_counter()
        status = "ok"
        extra: dict[str, object] = {}
        try:
            engine: Engine | None = request.app.state.engine
            if engine is None:
                raise LaneError(ErrorCode.INTERNAL, "Models are not loaded.")
            path = _check_path(settings, job.wav_path)
            passage_tokens = tokenize_passage(job.passage_content)
            if not passage_tokens:
                raise LaneError(ErrorCode.INVALID_JOB, "The passage has no words.")
            if job.deadline <= int(time.time() * 1000):
                raise LaneError(ErrorCode.DEADLINE_EXCEEDED, "Deadline exceeded.")

            def work() -> AudioWorkerResult:
                load_wav(path)  # fail fast on a bad file, before ASR's ffmpeg decode
                result = engine.pipeline.run(
                    str(path), job.passage_content, deadline_ms=job.deadline
                )
                extra.update(
                    error_count=result["score"]["error_count"],
                    phonetic_check=_phonetic_check_states(result),
                )
                mapped = to_worker_result(job.job_id, result, passage_tokens, engine.g2p)
                extra["miscue_count"] = len(mapped.miscues)
                return mapped

            jobs: JobRunner = request.app.state.jobs
            return await jobs.run(job.deadline, request.is_disconnected, work)
        except JobCancelled:
            status = "cancelled"
            raise LaneError(ErrorCode.INTERNAL, "Job was cancelled.") from None
        except MediaUnreadable:
            status = ErrorCode.MEDIA_UNREADABLE.value
            raise LaneError(
                ErrorCode.MEDIA_UNREADABLE, "Media file is missing or unreadable."
            ) from None
        except NoSpeech:
            status = ErrorCode.NO_SPEECH.value
            raise LaneError(ErrorCode.NO_SPEECH, "No speech in the recording.") from None
        except LaneError as exc:
            status = exc.code.value
            raise
        except Exception:
            status = ErrorCode.INTERNAL.value
            logger.exception("job failed")
            raise LaneError(ErrorCode.INTERNAL, "Internal error.") from None
        finally:
            logger.info(
                "job finished",
                extra={
                    "status": status,
                    "total_ms": round((time.perf_counter() - start) * 1000, 1),
                    "wav": Path(job.wav_path).name,
                    **extra,
                },
            )
            job_id_var.reset(token)

    @app.get("/health/ready", response_model=None)
    async def ready(request: Request) -> JSONResponse:
        if request.app.state.engine is not None:
            return JSONResponse({"status": "ready"})
        return _error_response(503, ErrorCode.INTERNAL, "Models are not loaded.")

    @app.get("/stats")
    async def stats(request: Request) -> dict[str, int | float | None]:
        jobs: JobRunner = request.app.state.jobs
        return {
            "queue_length": jobs.queue_length,
            "avg_processing_seconds": jobs.avg_processing_seconds,
        }

    return app
