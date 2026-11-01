import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request

from ..errors import MAIError
from ..models import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])

ReadinessCheck = Callable[[], Awaitable[bool]]


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, object]:
    """Runs the named checks in `app.state.readiness_checks`; any failure yields 503."""
    checks: dict[str, ReadinessCheck] = request.app.state.readiness_checks
    results: dict[str, bool] = {}
    for name, check in checks.items():
        try:
            results[name] = await check()
        except Exception:
            logger.exception("readiness check raised", extra={"check": name})
            results[name] = False
    failed = sorted(name for name, ok in results.items() if not ok)
    if failed:
        raise MAIError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "Service is not ready.",
            details={"failed_checks": failed},
        )
    return {"status": "ready", "checks": results}
