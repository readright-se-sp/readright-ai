"""is_word_by_word and is_disregard_punctuation, from Lane A's rows and the passage text.

Pure: no audio, no models. Both flags look at the silence between read words, so they share the
gap measurement. Not enough evidence (too few gaps or sentence ends) gives ``False`` with the
reason ``"insufficient"``.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from audio.alignment.text import normalize
from audio.config import AudioSettings
from audio.models import AlignedWord, Tag

# Rows that were actually read and timed. Omissions, insertions, fillers, false starts and
# off-text rows are left out: the pause next to them comes from the miscue, not the phrasing.
READ_TAGS: frozenset[Tag] = frozenset(
    {"match", "mispronunciation", "substitution", "repetition", "self_correction"}
)

Boundary = Literal["sentence", "comma"]

_CLOSERS = "\"'”’)]}"
_SENTENCE_END = ".!?;…"
_COMMA_END = ",:"


def punctuation_boundaries(passage_text: str) -> dict[int, Boundary]:
    """Passage word index -> the boundary that follows it.

    Walks ``text.split()`` and ``normalize()`` in the same order as ``tokenize_passage``, so the
    indices are the ones Lane A uses. A raw token ending in ``. ! ? ;`` closes a sentence after
    its last word; ``, :`` closes a comma phrase.
    """
    out: dict[int, Boundary] = {}
    index = 0
    for raw in passage_text.split():
        index += len(normalize(raw))
        mark = raw.rstrip(_CLOSERS)[-1:]
        if index == 0:
            continue
        if mark and mark in _SENTENCE_END:
            out[index - 1] = "sentence"
        elif mark and mark in _COMMA_END:
            out[index - 1] = "comma"
    return out


@dataclass
class Gaps:
    """Silences between consecutive read words, in seconds."""

    within: list[float] = field(default_factory=lambda: [])  # inside a phrase
    sentence: list[float] = field(default_factory=lambda: [])  # across a sentence end
    comma: list[float] = field(default_factory=lambda: [])  # across a comma


def _is_read(row: AlignedWord) -> bool:
    return (
        row["tag"] in READ_TAGS
        and row["start"] is not None
        and row["end"] is not None
        and bool(row["passage_indices"])
    )


def measure_gaps(rows: Sequence[AlignedWord], boundaries: dict[int, Boundary]) -> Gaps:
    """Gaps between neighbouring rows that were both read and timed.

    Rows without a passage index (a repetition) can't be placed against the punctuation, so the
    pairs they form are skipped.
    """
    gaps = Gaps()
    for a, b in zip(rows, rows[1:], strict=False):
        if not (_is_read(a) and _is_read(b)):
            continue
        last, first = max(a["passage_indices"]), min(b["passage_indices"])
        if first != last + 1 or a["end"] is None or b["start"] is None:
            continue
        gap = max(0.0, b["start"] - a["end"])
        kind = boundaries.get(last)
        if kind == "sentence":
            gaps.sentence.append(gap)
        elif kind == "comma":
            gaps.comma.append(gap)
        else:
            gaps.within.append(gap)
    return gaps


@dataclass(frozen=True)
class FlagOutcome:
    flag: bool
    metrics: dict[str, float | int | str | None]


def word_by_word(gaps: Gaps, settings: AudioSettings) -> FlagOutcome:
    """True when most within-phrase gaps are pauses (``WBW_GAP_MS`` or longer)."""
    n = len(gaps.within)
    pauses = sum(1 for g in gaps.within if g * 1000 >= settings.WBW_GAP_MS)
    share = pauses / n if n else None
    metrics: dict[str, float | int | str | None] = {
        "wbw_gaps": n,
        "wbw_pause_share": share,
        "wbw_median_gap_ms": statistics.median(gaps.within) * 1000 if n else None,
    }
    if n < settings.FLAGS_MIN_GAPS or share is None:
        metrics["wbw_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    return FlagOutcome(share >= settings.WBW_MIN_SHARE, metrics)


def disregard_punctuation(gaps: Gaps, settings: AudioSettings) -> FlagOutcome:
    """True when fewer than ``PUNCT_MIN_SHARE`` of the sentence ends get a real pause.

    A pause is real when it is at least ``PUNCT_SENTENCE_PAUSE_MS`` and ``PUNCT_REL`` times the
    reader's median within-phrase gap, so a reader who pauses everywhere doesn't pass.
    """
    median = statistics.median(gaps.within) if gaps.within else 0.0
    relative = settings.PUNCT_REL * median

    def honoured(values: list[float], floor_ms: int) -> int:
        return sum(1 for g in values if g * 1000 >= floor_ms and g >= relative)

    n = len(gaps.sentence)
    done = honoured(gaps.sentence, settings.PUNCT_SENTENCE_PAUSE_MS)
    commas = len(gaps.comma)
    commas_done = honoured(gaps.comma, settings.PUNCT_COMMA_PAUSE_MS)
    metrics: dict[str, float | int | str | None] = {
        "punct_sentence_boundaries": n,
        "punct_sentence_honoured_share": done / n if n else None,
        "punct_comma_boundaries": commas,
        "punct_comma_honoured_share": commas_done / commas if commas else None,
        "punct_median_within_gap_ms": median * 1000 if gaps.within else None,
    }
    if n < settings.FLAGS_MIN_BOUNDARIES:
        metrics["punct_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    return FlagOutcome(done / n < settings.PUNCT_MIN_SHARE, metrics)
