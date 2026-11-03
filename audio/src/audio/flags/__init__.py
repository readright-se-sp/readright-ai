"""The four audio flags: is_word_by_word, is_disregard_punctuation, is_monotonous,
is_hardly_audible.

``compute_flags`` never raises. A flag that fails is ``False`` and the error is logged.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from audio.config import AudioSettings
from audio.flags.acoustics import Samples, hardly_audible, monotonous
from audio.flags.fluency import (
    FlagOutcome,
    Gaps,
    disregard_punctuation,
    measure_gaps,
    punctuation_boundaries,
    word_by_word,
)
from audio.models import AlignedWord

logger = logging.getLogger(__name__)

Metrics = dict[str, float | int | str | None]


@dataclass(frozen=True)
class AudioFlags:
    word_by_word: bool = False
    disregard_punctuation: bool = False
    monotonous: bool = False
    hardly_audible: bool = False
    metrics: Metrics = field(default_factory=lambda: {})


def compute_flags(
    wav: Samples, rows: Sequence[AlignedWord], passage_text: str, settings: AudioSettings
) -> AudioFlags:
    """All four flags for one reading. ``FLAGS_ENABLED=false`` gives all false."""
    if not settings.FLAGS_ENABLED:
        return AudioFlags()
    metrics: Metrics = {}

    def run(name: str, compute: Callable[[], FlagOutcome]) -> bool:
        try:
            outcome = compute()
        except Exception:
            logger.exception("flag failed; treating it as false", extra={"flag": name})
            metrics[f"{name}_error"] = "failed"
            return False
        metrics.update(outcome.metrics)
        return outcome.flag

    gaps: Gaps | None = None

    def get_gaps() -> Gaps:
        nonlocal gaps
        if gaps is None:
            gaps = measure_gaps(rows, punctuation_boundaries(passage_text))
        return gaps

    return AudioFlags(
        word_by_word=run("word_by_word", lambda: word_by_word(get_gaps(), settings)),
        disregard_punctuation=run(
            "disregard_punctuation", lambda: disregard_punctuation(get_gaps(), settings)
        ),
        monotonous=run("monotonous", lambda: monotonous(wav, rows, settings)),
        hardly_audible=run("hardly_audible", lambda: hardly_audible(wav, rows, settings)),
        metrics=metrics,
    )
