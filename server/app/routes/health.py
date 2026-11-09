import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from ..errors import MAIError
from ..models import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])

ReadinessCheck = Callable[[], Awaitable[bool]]


def _iso(moment: datetime | None) -> str | None:
    """ISO-8601 UTC with milliseconds and a trailing Z, e.g. 2026-11-02T08:15:30.123Z."""
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@router.get("/live")
async def live(request: Request) -> dict[str, str | None]:
    """`last_request_at` is what the idle auto-stop script reads; probes never update it."""
    state = request.app.state
    return {
        "status": "ok",
        "started_at": _iso(state.started_at),
        "last_request_at": _iso(state.last_request_at),
    }


async def _run_check(name: str, check: ReadinessCheck, timeout: float) -> bool:
    try:
        return bool(await asyncio.wait_for(check(), timeout))
    except TimeoutError:
        logger.warning("readiness check timed out", extra={"check": name})
    except Exception:
        logger.exception("readiness check raised", extra={"check": name})
    return False


@router.get("/ready")
async def ready(request: Request) -> dict[str, object]:
    """Runs the named checks in `app.state.readiness_checks` concurrently; any failure is 503."""
    timeout = request.app.state.settings.dependency_timeout_seconds
    checks: dict[str, ReadinessCheck] = request.app.state.readiness_checks
    outcomes = await asyncio.gather(
        *(_run_check(name, check, timeout) for name, check in checks.items())
    )
    results = dict(zip(checks, outcomes, strict=True))
    failed = sorted(name for name, ok in results.items() if not ok)
    if failed:
        raise MAIError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "Service is not ready.",
            details={"failed_checks": failed},
        )
    return {"status": "ready", "checks": results}
