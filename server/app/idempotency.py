import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .errors import MAIError
from .models import AnalysisRequest, AnalysisResponse, ErrorCode

logger = logging.getLogger(__name__)

KEY_PREFIX = "mai:req:"

# Both scripts act only if the stored record still carries our token, so a request whose lease
# expired cannot overwrite or delete a newer claim. ARGV: token, [new value, ttl seconds].
_COMPLETE = """
local raw = redis.call('GET', KEYS[1])
if not raw or cjson.decode(raw).token ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
_RELEASE = """
local raw = redis.call('GET', KEYS[1])
if not raw or cjson.decode(raw).token ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


def request_fingerprint(body: AnalysisRequest) -> str:
    """Hash of what identifies a request. The video_url query (presigned signature) is left out
    because the BE presigns again on every retry."""
    url = body.video_url
    prefix = body.output_key_prefix
    port = f":{url.port}" if url.port else ""
    canonical = {
        "passage_title": body.passage_title,
        "passage_content": body.passage_content,
        "output_key_prefix": prefix.rstrip("/") if prefix is not None else None,
        "video_url": f"{url.scheme}://{url.host}{port}{url.path or ''}",
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _unavailable() -> MAIError:
    return MAIError(ErrorCode.SERVICE_UNAVAILABLE, "Idempotency store is unavailable.")


def _in_progress() -> MAIError:
    return MAIError(ErrorCode.REQUEST_IN_PROGRESS, "This request_id is already being processed.")


class IdempotencyStore:
    """Makes a `request_id` run at most once at a time and replayable once finished.

    One JSON record per request under `mai:req:<request_id>`, either `in_progress` (a lease
    with a TTL) or `done` (the response, kept for `result_ttl`). Failures delete the record,
    so the BE can retry with the same `request_id`.
    """

    def __init__(self, redis: Redis, in_progress_ttl: int, result_ttl: int) -> None:
        self._redis = redis
        self._in_progress_ttl = in_progress_ttl
        self._result_ttl = result_ttl

    @staticmethod
    def _key(body: AnalysisRequest) -> str:
        return f"{KEY_PREFIX}{body.request_id}"

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False

    async def lookup(self, body: AnalysisRequest) -> AnalysisResponse | None:
        """The stored result for a finished request, None if unknown.

        Raises 400 if the id was used with a different request, 409 if it is still running.
        """
        try:
            raw = await self._redis.get(self._key(body))
        except RedisError as exc:
            logger.error("idempotency lookup failed", extra={"error": type(exc).__name__})
            raise _unavailable() from exc
        return self._interpret(raw, body)

    async def claim(self, body: AnalysisRequest) -> str | AnalysisResponse:
        """Take the lease and return its token. If another request got there first (or
        finished) in the meantime, return the stored result or raise 409."""
        token = uuid.uuid4().hex
        record = {
            "state": "in_progress",
            "token": token,
            "body_hash": request_fingerprint(body),
            "claimed_at": _now_ms(),
        }
        try:
            claimed = await self._redis.set(
                self._key(body), json.dumps(record), nx=True, ex=self._in_progress_ttl
            )
            if claimed:
                return token
            raw = await self._redis.get(self._key(body))
        except RedisError as exc:
            logger.error("idempotency claim failed", extra={"error": type(exc).__name__})
            raise _unavailable() from exc
        replay = self._interpret(raw, body)
        if replay is not None:
            return replay
        raise _in_progress()  # the other holder vanished in between; let the BE retry

    async def complete(self, body: AnalysisRequest, token: str, result: AnalysisResponse) -> None:
        """Store the result. Never raises Redis errors: the work is done, so a failure is only
        logged (the lease expires and a retry reprocesses). Shielded so a cancelled request
        still stores its finished result."""
        await asyncio.shield(self._complete(body, token, result))

    async def _complete(self, body: AnalysisRequest, token: str, result: AnalysisResponse) -> None:
        record = {
            "state": "done",
            "token": token,
            "body_hash": request_fingerprint(body),
            "completed_at": _now_ms(),
            "response": result.model_dump(mode="json"),
        }
        try:
            stored = await self._redis.eval(  # type: ignore[misc]
                _COMPLETE, 1, self._key(body), token, json.dumps(record), self._result_ttl
            )
        except RedisError as exc:
            logger.error("storing result failed", extra={"error": type(exc).__name__})
            return
        if not stored:
            logger.warning("lease lost before completion; result not stored")

    async def release(self, body: AnalysisRequest, token: str) -> None:
        """Delete our claim after a failure. Never raises Redis errors; shielded so a
        cancelled request still releases."""
        await asyncio.shield(self._release(body, token))

    async def _release(self, body: AnalysisRequest, token: str) -> None:
        try:
            released = await self._redis.eval(_RELEASE, 1, self._key(body), token)  # type: ignore[misc]
        except RedisError as exc:
            logger.error("releasing claim failed", extra={"error": type(exc).__name__})
            return
        if not released:
            logger.warning("lease lost before release; newer record left alone")

    @staticmethod
    def _interpret(raw: str | None, body: AnalysisRequest) -> AnalysisResponse | None:
        if raw is None:
            return None
        record: dict[str, Any] = json.loads(raw)
        if record.get("body_hash") != request_fingerprint(body):
            raise MAIError(
                ErrorCode.INVALID_REQUEST,
                "request_id was already used with a different request.",
            )
        if record.get("state") != "done":
            raise _in_progress()
        try:
            return AnalysisResponse.model_validate(record["response"])
        except (KeyError, ValidationError):
            logger.error("stored result is unreadable")
            raise _unavailable() from None
