import asyncio
import logging
import time
from pathlib import Path

from pydantic import ValidationError

from .config import Settings
from .deadline import Deadline, deadline_var
from .download import download_video
from .errors import MAIError
from .media import convert_to_wav, probe
from .merge import merge_results
from .models import AnalysisRequest, AnalysisResponse, ErrorCode
from .scratch import scratch_dir
from .upload import upload_clips
from .workers import AudioLaneResult, VideoLaneResult, run_audio_lane, run_video_lane

logger = logging.getLogger(__name__)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def _run_lanes(
    body: AnalysisRequest, wav: Path, webm: Path, settings: Settings, deadline: Deadline
) -> tuple[AudioLaneResult, VideoLaneResult | None]:
    """Run both lanes at once. The first failure cancels the other lane and decides the error.

    The exception is a video failure with the audio-only flag on: then audio is awaited and the
    video result is None. A TIMEOUT from the video lane is never downgraded.
    """
    audio_task = asyncio.create_task(run_audio_lane(body, wav, settings, deadline))
    video_task = asyncio.create_task(run_video_lane(body, webm, settings, deadline))
    pending: set[asyncio.Task] = {audio_task, video_task}
    video_failed = False
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if error is None:
                    continue
                if (
                    task is video_task
                    and settings.allow_audio_only_on_video_failure
                    and isinstance(error, MAIError)
                    and error.code != ErrorCode.TIMEOUT
                ):
                    logger.warning(
                        "video lane failed; returning audio-only result",
                        extra={"error_code": error.code.value},
                    )
                    video_failed = True
                    continue
                raise error
        return audio_task.result(), None if video_failed else video_task.result()
    finally:
        for task in (audio_task, video_task):
            task.cancel()
        await asyncio.gather(audio_task, video_task, return_exceptions=True)


async def pipeline_processor(body: AnalysisRequest, settings: Settings) -> AnalysisResponse:
    """Download and preprocess the video, run both lanes in parallel, merge their results."""
    deadline = deadline_var.get() or Deadline(settings.request_deadline_seconds)
    with scratch_dir(settings, body.request_id) as work:
        video, audio = work / "input.webm", work / "audio.wav"

        started = time.monotonic()
        size = await download_video(
            str(body.video_url), video, max_bytes=settings.max_video_bytes, deadline=deadline
        )
        logger.info(
            "stage finished",
            extra={"stage": "download", "duration_ms": _ms(started), "bytes": size},
        )

        started = time.monotonic()
        await probe(video, settings)
        logger.info("stage finished", extra={"stage": "probe", "duration_ms": _ms(started)})

        started = time.monotonic()
        await convert_to_wav(video, audio, settings)
        logger.info("stage finished", extra={"stage": "convert", "duration_ms": _ms(started)})

        started = time.monotonic()
        audio_result, video_result = await _run_lanes(body, audio, video, settings, deadline)
        logger.info("stage finished", extra={"stage": "lanes", "duration_ms": _ms(started)})

        try:
            response = merge_results(body, settings, audio_result, video_result)
        except ValidationError:
            logger.exception("merging lane results failed")
            raise MAIError(
                ErrorCode.PROCESSING_FAILED,
                "The analysis could not be completed.",
                {"lane": "audio"},
            ) from None

        started = time.monotonic()
        await upload_clips(response, audio, settings, deadline)
        logger.info(
            "stage finished",
            extra={"stage": "upload", "duration_ms": _ms(started), "clips": len(response.miscues)},
        )
        return response
