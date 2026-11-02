import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .models import ErrorBody, ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_JOB: 400,
    ErrorCode.MEDIA_UNREADABLE: 422,
    ErrorCode.DEADLINE_EXCEEDED: 504,
    ErrorCode.INTERNAL: 500,
}


class LaneError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        return STATUS_BY_CODE[self.code]


def error_response(status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(body.model_dump(mode="json"), status)


async def _lane_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LaneError)
    return error_response(exc.status, exc.code, exc.message)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Generic message on purpose: never echo the submitted job_id or webm_path.
    return error_response(400, ErrorCode.INVALID_JOB, "Job body is invalid.")


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    response = error_response(exc.status_code, ErrorCode.INVALID_JOB, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return error_response(500, ErrorCode.INTERNAL, "Internal error.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LaneError, _lane_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
