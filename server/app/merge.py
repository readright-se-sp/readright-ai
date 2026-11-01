from .config import Settings
from .models import AnalysisRequest, AnalysisResponse, Miscue
from .workers import AudioLaneResult, VideoLaneResult


def output_key_prefix(body: AnalysisRequest, settings: Settings) -> str:
    prefix = body.output_key_prefix or settings.default_output_key_prefix.format(
        request_id=body.request_id
    )
    return prefix.rstrip("/") + "/"


def merge_results(
    body: AnalysisRequest,
    settings: Settings,
    audio: AudioLaneResult,
    video: VideoLaneResult | None,
) -> AnalysisResponse:
    """Combine the lane results. The video lane owns two flags; both are false without it."""
    prefix = output_key_prefix(body, settings)
    ordered = sorted(audio.miscues, key=lambda m: (m.start_ms, m.end_ms))
    miscues = [
        Miscue(
            actual_phrase=m.actual_phrase,
            spoken_phrase=m.spoken_phrase,
            actual_phoneme=m.actual_phoneme,
            spoken_phoneme=m.spoken_phoneme,
            read_start=m.start_ms,
            read_end=m.end_ms,
            audio_object_key=f"{prefix}miscue-{index:04d}.wav",
            miscue_type=m.miscue_type,
        )
        for index, m in enumerate(ordered, start=1)
    ]
    return AnalysisResponse(
        request_id=body.request_id,
        reading_score=audio.reading_score,
        wpm=audio.wpm,
        is_word_by_word=audio.is_word_by_word,
        is_disregard_punctuation=audio.is_disregard_punctuation,
        is_monotonous=audio.is_monotonous,
        is_lack_expression=video.is_lack_expression if video else False,
        is_hardly_audible=audio.is_hardly_audible,
        is_finger_pointing=video.is_finger_pointing if video else False,
        miscues=miscues,
    )
