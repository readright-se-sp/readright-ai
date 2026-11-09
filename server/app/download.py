import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .deadline import Deadline
from .errors import MAIError
from .models import ErrorCode

# httpx logs full request URLs (with the presigned signature) at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_EXPIRED_MARKER = b"Request has expired"
_ERROR_BODY_BYTES = 4096


async def download_video(url: str, dest: Path, *, max_bytes: int, deadline: Deadline) -> int:
    """Stream `url` to `dest`; returns bytes written. Never puts the URL in errors or logs."""
    host = urlparse(url).hostname
    remaining = deadline.remaining()
    timeout = httpx.Timeout(
        connect=min(10.0, remaining),
        read=min(30.0, remaining),
        write=min(30.0, remaining),
        pool=min(30.0, remaining),
    )
    too_big = MAIError(
        ErrorCode.VIDEO_DOWNLOAD_FAILED,
        "The video is larger than the allowed size.",
        details={"max_bytes": max_bytes},
    )
    written = 0
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            async with client.stream("GET", url) as response:
                status = response.status_code
                if not 200 <= status < 300:
                    if status == 403:
                        head = b""
                        async for chunk in response.aiter_bytes():
                            head += chunk
                            if len(head) >= _ERROR_BODY_BYTES:
                                break
                        if _EXPIRED_MARKER in head[:_ERROR_BODY_BYTES]:
                            raise MAIError(
                                ErrorCode.VIDEO_URL_EXPIRED, "The video URL has expired."
                            )
                    logger.warning(
                        "video download rejected",
                        extra={"video_host": host, "upstream_status": status},
                    )
                    raise MAIError(
                        ErrorCode.VIDEO_DOWNLOAD_FAILED,
                        "The video could not be downloaded.",
                        details={"upstream_status": status},
                    )
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                    raise too_big
                with dest.open("wb") as out:
                    async for chunk in response.aiter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise too_big
                        out.write(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        logger.warning(
            "video download failed",
            extra={"video_host": host, "error_type": type(exc).__name__},
        )
        if deadline.expired:
            raise MAIError(ErrorCode.TIMEOUT, "The request deadline was exceeded.") from None
        raise MAIError(
            ErrorCode.VIDEO_DOWNLOAD_FAILED, "The video could not be downloaded."
        ) from None
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written
