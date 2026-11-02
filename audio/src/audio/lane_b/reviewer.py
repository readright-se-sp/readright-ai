"""Lane B reviewer: plugs the phonetic lane into Lane A's ``RowReviewerProtocol`` hook.

Rows in (Lane A ``AlignedWord``) -> ``LaneAResult`` -> recogniser, join, arbitration -> new tags
written back onto the rows, with the evidence in ``row["phonetic"]``. Rows keep their order and
coverage; only ``tag`` and ``counted`` change. Any failure, or a passed deadline, returns the rows
unchanged: the request never fails because of Lane B.
"""

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from audio.alignment.labels import WordLabeler
from audio.alignment.text import is_proper_noun, normalize
from audio.arbitration.arbiter import arbitrate
from audio.config import AudioSettings
from audio.errors import DeadlineExceeded
from audio.lane_b.compare import hypothesis_variants
from audio.lane_b.join import join
from audio.models import AlignedWord, PhoneticEvidence, ReviewContext, Tag, retag
from audio.passage import PassagePreparer
from audio.phonetics.lexicon import load_lexicon
from audio.recognition.phoneme_recognizer import PhonemeRecognizer
from audio.types import (
    LaneAResult,
    PhoneticCheck,
    PreparedPassage,
    Unit,
    WordLabel,
    WordVerdict,
)

logger = logging.getLogger(__name__)

# Tags Lane B judges. Every other tag (repetition, swap, moved, reversal, filler, false_start,
# off_text, self_correction, letter_transposition) passes through untouched.
_TAG_TO_LABEL: dict[Tag, WordLabel] = {
    "match": WordLabel.CORRECT,
    "substitution": WordLabel.SUBSTITUTION,
    "mispronunciation": WordLabel.MISPRONUNCIATION,
    "omission": WordLabel.OMISSION,
    "insertion": WordLabel.INSERTION,
}
_LABEL_TO_TAG: dict[WordLabel, Tag] = {label: tag for tag, label in _TAG_TO_LABEL.items()}


@dataclass(frozen=True, slots=True)
class _Adapted:
    lane_a: LaneAResult
    row_index: tuple[int, ...]  # unit i came from rows[row_index[i]]


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _unit_for(row: AlignedWord, row_i: int) -> tuple[Unit, WordLabel] | None:
    """The unit for a judged row, or None when the row is not judged."""
    label = _TAG_TO_LABEL.get(row["tag"])
    if label is None:
        return None
    indices = row["passage_indices"]
    if label == WordLabel.INSERTION:
        if indices:
            return None
    elif not indices or indices != list(range(indices[0], indices[0] + len(indices))):
        return None  # no position, or not one contiguous stretch of the passage
    surface = row["spoken_word"] or ""
    start, end = row["start"], row["end"]
    timed = start is not None and end is not None
    start_ms = _ms(start) if timed and start is not None else None
    end_ms = max(_ms(end), start_ms) if timed and end is not None and start_ms is not None else None
    unit = Unit(
        surface=surface,
        key="".join(normalize(surface)),
        passage_index=indices[0] if indices else None,
        passage_span=len(indices),
        # unit-level only: Join checks "non-empty" to tell a heard word from an omission
        source_indices=(row_i,) if surface else (),
        start_ms=start_ms,
        end_ms=end_ms,
        align_score=1.0,
    )
    return unit, label


def adapt(rows: list[AlignedWord]) -> _Adapted:
    """Judged rows -> ``LaneAResult`` (seconds -> ms, untimed rows unverifiable)."""
    units: list[Unit] = []
    labels: list[WordLabel] = []
    index: list[int] = []
    for i, row in enumerate(rows):
        made = _unit_for(row, i)
        if made is not None:
            units.append(made[0])
            labels.append(made[1])
            index.append(i)
    lane_a = LaneAResult(units=tuple(units), labels=tuple(labels), trace=(), asr_words=())
    return _Adapted(lane_a, tuple(index))


def _in_lexicon(key: str, lexicon: Mapping[str, object]) -> bool:
    """A lexicon word, or its possessive (``juanings`` for ``juaning``)."""
    return key in lexicon or (key.endswith("s") and key[:-1] in lexicon)


def _unknown_name(unit: Unit, passage: PreparedPassage, names: frozenset[str]) -> bool:
    """True when the unit covers a proper noun that the lexicon has no pronunciation for (Q7)."""
    if unit.passage_index is None:
        return False
    lexicon = load_lexicon()
    tokens = passage.tokens[unit.passage_index : unit.passage_index + unit.passage_span]
    return any(is_proper_noun(t.key, names) and not _in_lexicon(t.key, lexicon) for t in tokens)


def _evidence(verdict: WordVerdict, check: PhoneticCheck, reason: str | None) -> PhoneticEvidence:
    best = verdict.best_variant_index
    expected = verdict.expected_phonemes[best] if best is not None else ()
    evidence = PhoneticEvidence(
        expected_phonemes=list(expected),
        heard_phonemes=list(verdict.heard_phonemes),
        d_exp=verdict.distance_expected,
        d_hyp=verdict.distance_hypothesis,
        check=check.value,
    )
    if reason:
        evidence["reason"] = reason
    return evidence


def _is_ending_row(row: AlignedWord, labeler: WordLabeler, names: frozenset[str]) -> bool:
    """Lane A calls this a mispronunciation with detail "ending" (``buys`` read "buy").

    Phil-IRI counts it as a mispronunciation, so Lane B may confirm it or clear it to a match,
    but never make it a substitution.
    """
    expected, heard = row["passage_word"], row["spoken_word"]
    if row["tag"] == "mispronunciation":
        return row["detail"] == "ending"
    if row["tag"] != "substitution" or not expected or not heard or " " in heard:
        return False
    label = labeler.label(expected, heard, names)
    return label.tag == "mispronunciation" and label.detail == "ending"


def _write_back(
    row: AlignedWord, tag: Tag, evidence: PhoneticEvidence, ending: bool = False
) -> AlignedWord:
    detail = None
    if ending and tag in ("substitution", "mispronunciation"):
        tag, detail = "mispronunciation", "ending"
    elif row["detail"] == "miscorrection" and tag != "match":
        detail = "miscorrection"
    if tag != row["tag"] or detail != row["detail"]:
        row = retag(row, tag, detail)
    else:
        row = row.copy()
    row["phonetic"] = evidence
    return row


class PhoneticReviewer:
    """Implements ``RowReviewerProtocol``: arbitrates Lane A's labels with phonetic evidence."""

    def __init__(
        self,
        recogniser: PhonemeRecognizer,
        passages: PassagePreparer,
        hypotheses: PassagePreparer,
        settings: AudioSettings,
        log: logging.Logger | None = None,
        labeler: WordLabeler | None = None,
    ) -> None:
        """``passages`` caches per passage; ``hypotheses`` (cache_size=0) prepares the ASR words."""
        self._recogniser = recogniser
        self._passages = passages
        self._hypotheses = hypotheses
        self._settings = settings
        self._log = log or logger
        self._labeler = labeler or WordLabeler()
        self.last_timing_ms: dict[str, int] = {}  # recognise_ms, total_ms of the latest review

    def review(
        self, wav_path: str, rows: list[AlignedWord], context: ReviewContext
    ) -> list[AlignedWord]:
        try:
            return self._review(wav_path, rows, context)
        except DeadlineExceeded:
            self._log.warning("phonetic_check=skipped reason=deadline")
        except Exception as exc:  # noqa: BLE001 - Lane B must never fail the request
            self._log.warning(
                "phonetic_check=skipped reason=%s: %s", type(exc).__name__, exc, exc_info=True
            )
        return rows

    def _review(
        self, wav_path: str, rows: list[AlignedWord], context: ReviewContext
    ) -> list[AlignedWord]:
        t0 = time.perf_counter()
        adapted = adapt(rows)
        if not adapted.lane_a.units:
            return rows
        passage = self._passages.prepare(context.passage_text)
        for unit in adapted.lane_a.units:
            if unit.passage_index is not None and (
                unit.passage_index + unit.passage_span > len(passage.tokens)
            ):
                raise ValueError("row passage indices are outside the prepared passage")

        phonemes = self._recogniser.recognise(Path(wav_path), context.deadline_ms)
        t_rec = time.perf_counter()
        s = self._settings
        joined = join(
            adapted.lane_a.units,
            phonemes,
            passage,
            pad_ms=s.JOIN_PAD_MS,
            mode=s.JOIN_MODE,
            max_reach_ms=s.JOIN_MAX_REACH_MS,
            coverage_min_ms=s.COVERAGE_MIN_MS,
            coverage_max_gap_ms=s.COVERAGE_MAX_GAP_MS,
        )
        hypothesis = hypothesis_variants(adapted.lane_a.units, self._hypotheses)
        result = arbitrate(
            adapted.lane_a,
            passage,
            joined,
            hypothesis,
            t_flag=s.T_FLAG,
            t_overrule=s.T_OVERRULE,
            coverage_guard_ratio=s.COVERAGE_GUARD_RATIO,
            adaptive_overrule=s.ADAPTIVE_OVERRULE,
        )

        out = list(rows)
        changed = 0
        for verdict, unit, row_i in zip(
            result.verdicts, adapted.lane_a.units, adapted.row_index, strict=True
        ):
            label, check, reason = verdict.label, verdict.phonetic_check, verdict.reason
            if (
                verdict.stage3_label == WordLabel.CORRECT
                and label == WordLabel.MISPRONUNCIATION
                and _unknown_name(unit, passage, context.proper_nouns)
            ):
                # G2P is unreliable for names outside the lexicon: never overrule on it.
                self._log.info(
                    "phonetic_check=unverifiable reason=name_not_in_lexicon row=%d", row_i
                )
                label, check = verdict.stage3_label, PhoneticCheck.UNVERIFIABLE
                reason = "name_not_in_lexicon"
            row = out[row_i]
            ending = check == PhoneticCheck.DONE and _is_ending_row(
                row, self._labeler, context.proper_nouns
            )
            new = _write_back(row, _LABEL_TO_TAG[label], _evidence(verdict, check, reason), ending)
            changed += new["tag"] != row["tag"]
            out[row_i] = new
        total_ms = round((time.perf_counter() - t0) * 1000)
        self.last_timing_ms = {"recognise_ms": round((t_rec - t0) * 1000), "total_ms": total_ms}
        self._log.info(
            "phonetic_check=done rows=%d changed=%d recognise_ms=%d total_ms=%d",
            len(adapted.lane_a.units),
            changed,
            self.last_timing_ms["recognise_ms"],
            total_ms,
        )
        return out
