import secrets

from fastapi.security import APIKeyHeader
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .errors import error_response
from .models import ErrorCode

# Documentation only: makes the generated OpenAPI schema show the scheme. Checking is done
# by ApiKeyMiddleware, which must run before the body is parsed.
api_key_scheme = APIKeyHeader(name="X-API-Key", scheme_name="ApiKeyAuth", auto_error=False)

_PROTECTED_PREFIX = "/v1/"


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._key = settings.api_key.get_secret_value().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(_PROTECTED_PREFIX):
            provided = Headers(scope=scope).get("x-api-key", "").encode()
            # Empty is rejected outright so an empty configured key could never match.
            if not provided or not secrets.compare_digest(provided, self._key):
                response = error_response(
                    401, ErrorCode.INVALID_REQUEST, "Missing or invalid API key."
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
