import logging

from fastapi import FastAPI

from . import __version__
from .auth import ApiKeyMiddleware
from .config import Settings, api_key_source, env_file_overrides
from .errors import register_error_handlers
from .logging import RequestLoggingMiddleware, configure_logging
from .routes import analyses, health


def log_settings_sources() -> None:
    """Say where settings came from, so a stale shell variable isn't silent. Never logs values."""
    logger = logging.getLogger("app.config")
    for name in env_file_overrides():
        logger.warning("environment variable overrides .env", extra={"setting": name})
    logger.info("api key loaded", extra={"source": api_key_source()})


def create_app(settings: Settings | None = None) -> FastAPI:
    from_environment = settings is None
    settings = settings or Settings()  # type: ignore[call-arg]  # api_key comes from env
    configure_logging(settings.log_level)
    if from_environment:
        log_settings_sources()

    app = FastAPI(title="ReadRight MAI", version=__version__)
    app.state.settings = settings
    app.state.readiness_checks = {}

    # Last added is outermost: logging wraps auth so 401s are logged too.
    app.add_middleware(ApiKeyMiddleware, settings=settings)
    app.add_middleware(RequestLoggingMiddleware)

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(analyses.router)
    return app
