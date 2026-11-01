import re

from .config import Settings
from .models import AnalysisRequest, AnalysisResponse, Miscue, MiscueType

# Words may contain inner apostrophes ("Pam's"); surrounding punctuation and quotes are dropped.
_WORD = re.compile(r"\w+(?:['’]\w+)*")


def build_stub_analysis(body: AnalysisRequest, settings: Settings) -> AnalysisResponse:
    """Deterministic dummy result derived from the request. Replaced by the real pipeline later."""
    prefix = body.output_key_prefix or settings.default_output_key_prefix.format(
        request_id=body.request_id
    )
    prefix = prefix.rstrip("/") + "/"

    words = _WORD.findall(body.passage_content)
    miscues: list[Miscue] = []

    if words:
        actual = words[0]
        spoken = actual[:-1] if len(actual) > 1 else "uh"
        miscues.append(
            Miscue(
                actual_phrase=actual,
                spoken_phrase=spoken,
                actual_phoneme=list(actual),
                spoken_phoneme=list(spoken),
                read_start=500,
                read_end=1000,
                audio_object_key="",
                miscue_type=MiscueType.SUBSTITUTION,
            )
        )
    if len(words) >= 3:
        miscues.append(
            Miscue(
                actual_phrase=words[2],
                spoken_phrase="",
                actual_phoneme=list(words[2]),
                spoken_phoneme=[],
                read_start=2000,
                read_end=2400,
                audio_object_key="",
                miscue_type=MiscueType.OMISSION,
            )
        )
    for index, miscue in enumerate(miscues, start=1):
        miscue.audio_object_key = f"{prefix}miscue-{index:04d}.wav"

    return AnalysisResponse(
        request_id=body.request_id,
        reading_score=85.0,
        wpm=60.0,
        is_word_by_word=False,
        is_disregard_punctuation=True,
        is_monotonous=False,
        is_lack_expression=False,
        is_hardly_audible=False,
        is_finger_pointing=False,
        miscues=miscues,
    )
