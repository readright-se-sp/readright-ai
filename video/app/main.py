import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .analyzer import Analyzer, JobCancelled, PlaceholderAnalyzer
from .config import Settings
from .errors import LaneError, error_response, install_error_handlers
from .jobs import JobRunner
from .logging import configure_logging, job_id_var
from .models import ErrorCode, VideoJob, VideoResult

logger = logging.getLogger(__name__)


async def _load_analyzer(analyzer: Analyzer) -> None:
    try:
        await asyncio.to_thread(analyzer.load)
    except Exception:
        logger.exception("analyzer load failed; lane stays not ready")


def _check_path(settings: Settings, webm_path: str) -> Path:
    path = Path(webm_path)
    if not path.is_absolute():
        raise LaneError(ErrorCode.INVALID_JOB, "webm_path must be an absolute path.")
    if "\x00" in webm_path:
        raise LaneError(ErrorCode.INVALID_JOB, "webm_path is invalid.")
    if settings.allowed_media_dir is not None:
        try:
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError:
                resolved = path.resolve()  # missing file: the file check answers 422
            inside = resolved.is_relative_to(settings.allowed_media_dir.resolve())
        except (ValueError, OSError, RuntimeError):
            raise LaneError(ErrorCode.INVALID_JOB, "webm_path is invalid.") from None
        if not inside:
            raise LaneError(ErrorCode.INVALID_JOB, "webm_path is outside the media directory.")
    return path


def create_app(settings: Settings | None = None, analyzer: Analyzer | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    analyzer = analyzer or PlaceholderAnalyzer()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.analyzer = analyzer
        app.state.jobs = JobRunner(settings, analyzer)
        load_task = asyncio.create_task(_load_analyzer(analyzer))
        try:
            yield
        finally:
            load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await load_task

    app = FastAPI(title="ReadRight video lane", version=__version__, lifespan=lifespan)
    install_error_handlers(app)

    @app.post("/v1/jobs")
    async def create_job(job: VideoJob, request: Request) -> VideoResult:
        token = job_id_var.set(job.job_id)
        start = time.perf_counter()
        status = "ok"
        try:
            if job.deadline <= int(time.time() * 1000):
                raise LaneError(ErrorCode.DEADLINE_EXCEEDED, "Deadline exceeded.")
            path = _check_path(settings, job.webm_path)
            if not path.is_file():
                raise LaneError(ErrorCode.MEDIA_UNREADABLE, "Media file is missing or unreadable.")
            flags = await request.app.state.jobs.run(job, request.is_disconnected)
            return VideoResult(
                job_id=job.job_id,
                is_finger_pointing=flags.is_finger_pointing,
                is_lack_expression=flags.is_lack_expression,
            )
        except JobCancelled:
            status = "cancelled"
            raise LaneError(ErrorCode.INTERNAL, "Job was cancelled.") from None
        except LaneError as exc:
            status = exc.code.value
            raise
        except Exception:
            status = ErrorCode.INTERNAL.value
            logger.exception("job failed")
            raise
        finally:
            logger.info(
                "job finished",
                extra={
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            job_id_var.reset(token)

    @app.get("/health/ready", response_model=None)
    async def ready(request: Request) -> JSONResponse:
        if request.app.state.analyzer.ready:
            return JSONResponse({"status": "ready"})
        return error_response(503, ErrorCode.INTERNAL, "Models are not loaded.")

    @app.get("/stats")
    async def stats(request: Request) -> dict[str, int | float | None]:
        jobs: JobRunner = request.app.state.jobs
        return {
            "queue_length": jobs.queue_length,
            "avg_processing_seconds": jobs.avg_processing_seconds,
        }

    return app
