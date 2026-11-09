import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .models import ErrorBody, ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNSUPPORTED_MEDIA: 415,
    ErrorCode.VIDEO_URL_EXPIRED: 422,
    ErrorCode.VIDEO_DOWNLOAD_FAILED: 422,
    ErrorCode.NO_SPEECH_DETECTED: 422,
    ErrorCode.REQUEST_IN_PROGRESS: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PROCESSING_FAILED: 500,
    ErrorCode.UPLOAD_FAILED: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.TIMEOUT: 504,
}

DEFAULT_RETRY_AFTER = 5
_RETRY_AFTER_CODES = {ErrorCode.RATE_LIMITED, ErrorCode.SERVICE_UNAVAILABLE}


class MAIError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.retry_after = retry_after

    @property
    def status(self) -> int:
        return STATUS_BY_CODE[self.code]


def error_response(
    status: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    if retry_after is None and code in _RETRY_AFTER_CODES:
        retry_after = DEFAULT_RETRY_AFTER
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status, headers)


async def _mai_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MAIError)
    return error_response(exc.status, exc.code, exc.message, exc.details, exc.retry_after)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Deliberately drop `input` and `ctx`: they may contain the presigned URL signature.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return error_response(
        400, ErrorCode.INVALID_REQUEST, "Request validation failed.", {"errors": errors}
    )


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    response = error_response(exc.status_code, ErrorCode.INVALID_REQUEST, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(MAIError, _mai_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
