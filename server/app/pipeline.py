import logging
import time

from .config import Settings
from .deadline import Deadline, deadline_var
from .download import download_video
from .media import convert_to_wav, probe
from .models import AnalysisRequest, AnalysisResponse
from .scratch import scratch_dir
from .stub import build_stub_analysis

logger = logging.getLogger(__name__)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def pipeline_processor(body: AnalysisRequest, settings: Settings) -> AnalysisResponse:
    """Download and preprocess the video. Lanes arrive in Phase 4; the result is still a stub."""
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

        return build_stub_analysis(body, settings)
