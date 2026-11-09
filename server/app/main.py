import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI

from . import __version__
from .admission import Admission, WorkerStatsSource, cached_worker_stats_source
from .auth import ApiKeyMiddleware
from .config import Settings, api_key_source, env_file_overrides
from .errors import register_error_handlers
from .idempotency import IdempotencyStore
from .logging import RequestLoggingMiddleware, configure_logging
from .models import AnalysisRequest, AnalysisResponse
from .pipeline import pipeline_processor
from .readiness import SharedS3Client, ffmpeg_ready, s3_ready, worker_ready
from .routes import analyses, health
from .routes.health import ReadinessCheck

Processor = Callable[[AnalysisRequest, Settings], Awaitable[AnalysisResponse]]


def log_settings_sources() -> None:
    """Say where settings came from, so a stale shell variable isn't silent. Never logs values."""
    logger = logging.getLogger("app.config")
    for name in env_file_overrides():
        logger.warning("environment variable overrides .env", extra={"setting": name})
    logger.info("api key loaded", extra={"source": api_key_source()})


def create_app(
    settings: Settings | None = None,
    redis: aioredis.Redis | None = None,
    processor: Processor | None = None,
    worker_stats: WorkerStatsSource | None = None,
    dependency_checks: dict[str, ReadinessCheck] | None = None,
) -> FastAPI:
    from_environment = settings is None
    settings = settings or Settings()  # type: ignore[call-arg]  # api_key comes from env
    configure_logging(settings.log_level)
    if from_environment:
        log_settings_sources()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = redis
        if client is None:
            url = settings.redis_url.get_secret_value()
            client = aioredis.from_url(
                url,
                decode_responses=True,
                socket_timeout=settings.redis_timeout_seconds,
                socket_connect_timeout=settings.redis_timeout_seconds,
            )
            # Host only: the URL may hold a password. No PING here; /health/ready reports it.
            logging.getLogger("app.config").info(
                "redis client created", extra={"redis_host": urlparse(url).hostname}
            )
        store = IdempotencyStore(
            client, settings.in_progress_ttl_seconds, settings.result_ttl_seconds
        )
        app.state.idempotency = store
        app.state.processor = processor or pipeline_processor
        for binary in (settings.ffmpeg_path, settings.ffprobe_path):
            if shutil.which(binary) is None:
                logging.getLogger("app.config").warning(
                    "media binary not found; requests will fail", extra={"binary": binary}
                )
        if not settings.s3_bucket:
            logging.getLogger("app.config").warning(
                "MAI_S3_BUCKET is not set; requests with miscues will fail with UPLOAD_FAILED"
            )
        probe_http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.dependency_timeout_seconds)
        )  # shared by the worker checks; closed below
        shared_s3 = SharedS3Client(settings)
        if dependency_checks is None:
            checks: dict[str, ReadinessCheck] = {
                "audio_worker": lambda: worker_ready(
                    settings.audio_worker_url, settings, probe_http
                ),
                "video_worker": lambda: worker_ready(
                    settings.video_worker_url, settings, probe_http
                ),
                "s3": lambda: s3_ready(settings, shared_s3),
                "ffmpeg": lambda: ffmpeg_ready(settings),
            }
        else:
            checks = dependency_checks
        app.state.readiness_checks.update(checks)
        app.state.readiness_checks["redis"] = store.ping
        try:
            yield
        finally:
            await probe_http.aclose()
            await asyncio.to_thread(shared_s3.close)
            if redis is None:  # close only a client we created
                await client.aclose()

    app = FastAPI(title="ReadRight MAI", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.readiness_checks = {}
    app.state.admission = Admission(settings, worker_stats or cached_worker_stats_source(settings))
    app.state.started_at = datetime.now(UTC)
    app.state.last_request_at = None  # set by POST /v1/analyses; read by /health/live

    # Last added is outermost: logging wraps auth so 401s are logged too.
    app.add_middleware(ApiKeyMiddleware, settings=settings)
    app.add_middleware(RequestLoggingMiddleware)

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(analyses.router)
    return app
