import asyncio
import logging
import time
import wave
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .clips import cut_clip
from .config import Settings
from .deadline import Deadline
from .errors import MAIError
from .models import AnalysisResponse, ErrorCode, Miscue

logger = logging.getLogger(__name__)


def _upload_failed() -> MAIError:
    return MAIError(ErrorCode.UPLOAD_FAILED, "The mini audio files could not be uploaded.")


def _error_name(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code") or "ClientError")
    return type(exc).__name__


async def upload_clips(
    response: AnalysisResponse, wav: Path, settings: Settings, deadline: Deadline
) -> None:
    """Cut one clip per miscue and PUT it to S3 at the miscue's audio_object_key."""
    if not response.miscues:
        return
    if not settings.s3_bucket:
        logger.error("cannot upload clips: MAI_S3_BUCKET is not set")
        raise _upload_failed()

    limit = max(1.0, deadline.remaining())
    try:
        client = await asyncio.to_thread(
            boto3.client,
            "s3",
            region_name=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url,
            config=Config(connect_timeout=limit, read_timeout=limit, retries={"max_attempts": 2}),
        )
    except Exception as exc:
        logger.warning("clip upload failed", extra={"error": _error_name(exc)})
        raise _upload_failed() from None
    bucket = settings.s3_bucket
    semaphore = asyncio.Semaphore(settings.upload_concurrency)

    async def upload(miscue: Miscue) -> None:
        async with semaphore:
            try:
                body = await asyncio.to_thread(
                    cut_clip, wav, miscue.read_start, miscue.read_end, settings.clip_padding_ms
                )
            except MAIError:
                raise
            except (wave.Error, EOFError, OSError) as exc:
                logger.warning("clip cut failed", extra={"error": type(exc).__name__})
                raise MAIError(
                    ErrorCode.PROCESSING_FAILED,
                    "The analysis could not be completed.",
                    {"lane": "audio"},
                ) from None
            try:
                await asyncio.to_thread(
                    client.put_object,
                    Bucket=bucket,
                    Key=miscue.audio_object_key,
                    Body=body,
                    ContentType="audio/wav",
                )
            except Exception as exc:
                logger.warning(
                    "clip upload failed",
                    extra={"key": miscue.audio_object_key, "error": _error_name(exc)},
                )
                raise _upload_failed() from None

    started = time.monotonic()
    try:
        async with asyncio.timeout(deadline.remaining()):
            try:
                async with asyncio.TaskGroup() as group:
                    for miscue in response.miscues:
                        group.create_task(upload(miscue))
            except* MAIError as group_error:
                raise group_error.exceptions[0] from None
    except TimeoutError:
        logger.warning("clip upload timed out", extra={"clips": len(response.miscues)})
        raise MAIError(ErrorCode.TIMEOUT, "The request timed out.") from None
    finally:
        client.close()
    logger.debug(
        "clips uploaded",
        extra={
            "clips": len(response.miscues),
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    )
