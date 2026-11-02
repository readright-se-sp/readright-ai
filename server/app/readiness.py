import asyncio
import logging
import shutil
from typing import Any

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

from .admission import fetch_worker_stats
from .config import Settings

logger = logging.getLogger(__name__)


async def worker_ready(
    base_url: str, settings: Settings, client: httpx.AsyncClient | None = None
) -> bool:
    """Ready when `/health/ready` is 200 and `/stats` returns a valid body.

    `client` is the app's shared client; without one a short-lived client is used.
    """
    host = "?"
    timeout = httpx.Timeout(settings.dependency_timeout_seconds)
    own = client is None
    http = httpx.AsyncClient(timeout=timeout) if client is None else client
    try:
        host = httpx.URL(base_url).host or "?"
        response = await http.get(f"{base_url.rstrip('/')}/health/ready", timeout=timeout)
        if response.status_code != 200:
            return False
        await fetch_worker_stats(base_url, settings, http)
    except Exception as exc:
        logger.warning("worker not ready", extra={"host": host, "error": type(exc).__name__})
        return False
    finally:
        if own:
            await http.aclose()
    return True


def _new_s3_client(settings: Settings) -> Any:
    timeout = settings.dependency_timeout_seconds
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.s3_endpoint_url,
        config=Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 1}),
    )


class SharedS3Client:
    """One boto3 client for all readiness probes, created on first use off the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._lock = asyncio.Lock()

    async def get(self) -> Any:
        async with self._lock:
            if self._client is None:
                self._client = await asyncio.to_thread(_new_s3_client, self._settings)
            return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


async def s3_ready(settings: Settings, shared: SharedS3Client | None = None) -> bool:
    """HeadBucket on `s3_bucket`. `shared` reuses the app's client; otherwise one per call."""
    bucket = settings.s3_bucket
    if not bucket:
        return False
    try:
        if shared is not None:
            client = await shared.get()
            await asyncio.to_thread(client.head_bucket, Bucket=bucket)
        else:
            client = await asyncio.to_thread(_new_s3_client, settings)
            try:
                await asyncio.to_thread(client.head_bucket, Bucket=bucket)
            finally:
                close = getattr(client, "close", None)
                if close is not None:
                    close()
    except Exception as exc:
        if isinstance(exc, ClientError):
            error = str(exc.response.get("Error", {}).get("Code") or "ClientError")
        else:
            error = type(exc).__name__
        logger.warning("s3 not ready", extra={"error": error})
        return False
    return True


async def ffmpeg_ready(settings: Settings) -> bool:
    binaries = (settings.ffmpeg_path, settings.ffprobe_path)
    return all(shutil.which(binary) is not None for binary in binaries)
