import logging
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Self, TypeVar

import httpx
from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .deadline import Deadline
from .errors import MAIError
from .models import AnalysisRequest, ErrorCode, MiscueType

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LaneMiscue(BaseModel):
    actual_phrase: str
    spoken_phrase: str
    actual_phoneme: list[str]
    spoken_phoneme: list[str]
    start_ms: int = Field(ge=0)
    end_ms: int
    miscue_type: MiscueType

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must not be less than start_ms")
        return self


class AudioLaneResult(BaseModel):
    job_id: str
    reading_score: Annotated[float, Field(allow_inf_nan=False, ge=0, le=100)]
    wpm: Annotated[float, Field(allow_inf_nan=False, ge=0)]
    is_word_by_word: bool
    is_disregard_punctuation: bool
    is_monotonous: bool
    is_hardly_audible: bool
    miscues: list[LaneMiscue]


class VideoLaneResult(BaseModel):
    job_id: str
    is_finger_pointing: bool
    is_lack_expression: bool


def deadline_epoch_ms(deadline: Deadline) -> int:
    """The deadline as absolute UTC epoch milliseconds, the form the lanes receive."""
    return int((time.time() + deadline.remaining()) * 1000)


def _worker_error(response: httpx.Response) -> tuple[str | None, str | None]:
    """(code, message) from a worker error body, each parsed alone; None if not a string."""
    try:
        error = response.json()["error"]
        code, message = error.get("code"), error.get("message")
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, None
    return (code if isinstance(code, str) else None), (
        message if isinstance(message, str) else None
    )


async def _call_lane(
    lane: Literal["audio", "video"],
    base_url: str,
    payload: dict[str, Any],
    result_model: type[T],
    settings: Settings,
    deadline: Deadline,
    job_id: str,
) -> T:
    remaining = deadline.remaining()
    timeout = httpx.Timeout(
        connect=min(settings.worker_connect_timeout_seconds, remaining),
        read=remaining,
        write=remaining,
        pool=remaining,
    )
    failed = MAIError(
        ErrorCode.PROCESSING_FAILED, "The analysis could not be completed.", {"lane": lane}
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url.rstrip('/')}/v1/jobs", json=payload)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning(
            "lane request failed", extra={"lane": lane, "error_type": type(exc).__name__}
        )
        if deadline.expired:
            raise MAIError(ErrorCode.TIMEOUT, "The request deadline was exceeded.") from None
        raise failed from None

    if not response.is_success:
        code, message = _worker_error(response)
        logger.warning(
            "lane rejected the job",
            extra={
                "lane": lane,
                "worker_status": response.status_code,
                "worker_code": code,
                "worker_message": message,
            },
        )
        if lane == "audio" and code == "NO_SPEECH":
            raise MAIError(ErrorCode.NO_SPEECH_DETECTED, "No speech was detected in the recording.")
        if code == "DEADLINE_EXCEEDED":
            raise MAIError(ErrorCode.TIMEOUT, "The request deadline was exceeded.")
        raise failed

    try:
        result = result_model.model_validate(response.json())
    except ValueError:  # bad JSON or a pydantic ValidationError (a ValueError subclass)
        logger.warning("lane returned an invalid result", extra={"lane": lane})
        raise failed from None
    # Once lanes batch jobs, a mixed-up response must not reach another student.
    if str(result.job_id).lower() != job_id.lower():
        logger.warning("lane returned a result for another job", extra={"lane": lane})
        raise failed
    return result


async def run_audio_lane(
    body: AnalysisRequest, wav_path: Path, settings: Settings, deadline: Deadline
) -> AudioLaneResult:
    payload = {
        "job_id": str(body.request_id),
        "deadline": deadline_epoch_ms(deadline),
        "wav_path": str(wav_path),
        "passage_title": body.passage_title,
        "passage_content": body.passage_content,
    }
    return await _call_lane(
        "audio",
        settings.audio_worker_url,
        payload,
        AudioLaneResult,
        settings,
        deadline,
        payload["job_id"],
    )


async def run_video_lane(
    body: AnalysisRequest, webm_path: Path, settings: Settings, deadline: Deadline
) -> VideoLaneResult:
    payload = {
        "job_id": str(body.request_id),
        "deadline": deadline_epoch_ms(deadline),
        "webm_path": str(webm_path),
    }
    return await _call_lane(
        "video",
        settings.video_worker_url,
        payload,
        VideoLaneResult,
        settings,
        deadline,
        payload["job_id"],
    )
