"""Shared, validated types for every hand-off between audio-lane stages (interim, Phase B0).

All models are frozen with ``extra="forbid"`` and use tuples, so instances are immutable.
All times are integer milliseconds from the start of the WAV.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)


def _check_symbol(symbol: str) -> str:
    if not symbol or any(c.isspace() for c in symbol):
        raise ValueError("phoneme symbols must be non-empty and contain no whitespace")
    return symbol


def _check_deviation_symbol(symbol: str) -> str:
    # "" is allowed here: it marks the missing side of an insertion or a deletion.
    if any(c.isspace() for c in symbol):
        raise ValueError("phoneme symbols must not contain whitespace")
    return symbol


Symbol = Annotated[str, AfterValidator(_check_symbol)]
PhoneSeq = tuple[Symbol, ...]  # IPA segments, already normalised (no stress, no tie bars)
Ms = Annotated[int, Field(ge=0)]
Unit01 = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Distance = Annotated[float, Field(ge=0, allow_inf_nan=False)]
NonNegFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class WordLabel(StrEnum):
    # values equal the contract MiscueType values, plus "correct"
    CORRECT = "correct"
    MISPRONUNCIATION = "mispronunciation"
    OMISSION = "omission"
    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    REPETITION = "repetition"
    TRANSPOSITION = "transposition"
    REVERSAL = "reversal"
    SELF_CORRECTION = "self-correction"


STAGE3_LABELS: frozenset[WordLabel] = frozenset(
    {
        WordLabel.CORRECT,
        WordLabel.SUBSTITUTION,
        WordLabel.MISPRONUNCIATION,
        WordLabel.OMISSION,
        WordLabel.INSERTION,
    }
)
ARBITRATION_LABELS = STAGE3_LABELS  # arbitration never produces the post-pass labels


class PhoneticCheck(StrEnum):
    DONE = "done"
    SKIPPED = "skipped"
    UNVERIFIABLE = "unverifiable"


class AlignOp(StrEnum):
    MATCH = "match"
    SUB = "sub"
    DELETE = "delete"
    INSERT = "insert"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_times(start_ms: int | None, end_ms: int | None) -> None:
    if (start_ms is None) != (end_ms is None):
        raise ValueError("start_ms and end_ms must both be set or both be None")
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise ValueError("end_ms must be >= start_ms")


# --- Stage 0 output (Lane B) ---


class PassageToken(_Model):
    index: Annotated[int, Field(ge=0)]
    raw: str  # passage text as written
    key: str  # normalised form


class PreparedPassage(_Model):
    passage_hash: str
    tokens: tuple[PassageToken, ...]
    pronunciations: Mapping[str, tuple[PhoneSeq, ...]]

    @field_serializer("pronunciations")
    def _serialize_pronunciations(self, value: Mapping[str, tuple[PhoneSeq, ...]]) -> dict:
        return dict(value)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for i, token in enumerate(self.tokens):
            if token.index != i:
                raise ValueError(f"tokens[{i}].index must equal {i}, got {token.index}")
            variants = self.pronunciations.get(token.key)
            if not variants or not any(variants):
                raise ValueError(f"token key {token.key!r} needs a non-empty pronunciation variant")
        # Read-only view; frozen models do not block mutation of a plain dict.
        object.__setattr__(self, "pronunciations", MappingProxyType(dict(self.pronunciations)))
        return self


# --- Lane A output (owned by Lane A, interim definition) ---


class AsrWord(_Model):
    index: Annotated[int, Field(ge=0)]
    text: str
    start_ms: Ms | None
    end_ms: Ms | None
    score: Unit01

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_times(self.start_ms, self.end_ms)
        return self


class TraceStep(_Model):
    op: AlignOp
    passage_index: Annotated[int, Field(ge=0)] | None = None
    asr_index: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        has_p, has_a = self.passage_index is not None, self.asr_index is not None
        expected = {
            AlignOp.MATCH: (True, True),
            AlignOp.SUB: (True, True),
            AlignOp.DELETE: (True, False),
            AlignOp.INSERT: (False, True),
        }[self.op]
        if (has_p, has_a) != expected:
            raise ValueError(f"indices do not fit op {self.op.value!r}")
        return self


class Unit(_Model):
    """One aligned position."""

    surface: str  # the ASR text; "" for an omission
    key: str
    # First passage token covered; None for an insertion. Not in the Rev 2 spec; added so
    # Lane B can look up the expected phonemes.
    passage_index: Annotated[int, Field(ge=0)] | None
    passage_span: Annotated[int, Field(ge=0)]  # >= 1 normally; 0 for an insertion
    source_indices: tuple[Annotated[int, Field(ge=0)], ...]  # ASR word indices; () for omission
    start_ms: Ms | None
    end_ms: Ms | None
    align_score: Annotated[float, Field(allow_inf_nan=False)]

    @property
    def verifiable(self) -> bool:
        return self.start_ms is not None and self.end_ms is not None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_times(self.start_ms, self.end_ms)
        if self.passage_index is None and self.passage_span != 0:
            raise ValueError("an insertion (passage_index None) must have passage_span 0")
        if self.passage_index is not None and self.passage_span < 1:
            raise ValueError("passage_span must be >= 1 when passage_index is set")
        return self


class LaneAResult(_Model):
    units: tuple[Unit, ...]
    labels: tuple[WordLabel, ...]
    trace: tuple[TraceStep, ...]
    asr_words: tuple[AsrWord, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(self.labels) != len(self.units):
            raise ValueError("labels and units must have the same length")
        bad = [label for label in self.labels if label not in STAGE3_LABELS]
        if bad:
            raise ValueError(f"labels outside STAGE3_LABELS: {sorted(b.value for b in bad)}")
        next_free = 0  # first passage token not yet claimed by an earlier unit
        for unit in self.units:
            if unit.passage_index is None:
                continue
            if unit.passage_index < next_free:
                raise ValueError("units must be in passage order and must not overlap")
            next_free = unit.passage_index + unit.passage_span
        return self


# --- Lane B intermediate ---


class PhonemeToken(_Model):
    symbol: Symbol
    start_ms: Ms
    end_ms: Ms
    confidence: Unit01

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_times(self.start_ms, self.end_ms)
        return self


class CoverageCandidate(_Model):
    start_ms: Ms
    end_ms: Ms
    phonemes: tuple[PhonemeToken, ...]
    after_passage_index: Annotated[int, Field(ge=0)] | None  # None: before the first word

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_times(self.start_ms, self.end_ms)
        return self


class JoinResult(_Model):
    unit_phonemes: tuple[tuple[PhonemeToken, ...], ...]  # one entry per unit, in unit order
    coverage_candidates: tuple[CoverageCandidate, ...]  # in time order
    discarded: tuple[PhonemeToken, ...]  # runs shorter than COVERAGE_MIN_MS
    ms_per_phoneme: tuple[NonNegFloat | None, ...]  # one entry per unit

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(self.unit_phonemes) != len(self.ms_per_phoneme):
            raise ValueError("unit_phonemes and ms_per_phoneme must have the same length")
        return self


# --- Arbitration output (input to the post-pass) ---

Deviation = tuple[
    Annotated[str, AfterValidator(_check_deviation_symbol)],
    Annotated[str, AfterValidator(_check_deviation_symbol)],
]


class Comparison(_Model):
    """Best-variant result of Compare (B4)."""

    distance: Distance
    best_variant_index: Annotated[int, Field(ge=0)]
    deviations: tuple[Deviation, ...]


class WordVerdict(_Model):
    unit_index: Annotated[int, Field(ge=0)]
    expected_phonemes: tuple[PhoneSeq, ...]
    best_variant_index: Annotated[int, Field(ge=0)] | None
    heard_phonemes: PhoneSeq
    distance_expected: Distance | None
    distance_hypothesis: Distance | None
    # ("", x) is an insertion, (x, "") a deletion
    deviations: tuple[Deviation, ...]
    stage3_label: WordLabel
    label: WordLabel
    phonetic_check: PhoneticCheck
    # why the check is not "done": untimed, no_heard, low_coverage (B6a); None otherwise
    reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.best_variant_index is not None and self.best_variant_index >= len(
            self.expected_phonemes
        ):
            raise ValueError("best_variant_index is out of range of expected_phonemes")
        if any(a == "" and b == "" for a, b in self.deviations):
            raise ValueError("a deviation needs at least one non-empty side")
        if self.stage3_label not in STAGE3_LABELS:
            raise ValueError("stage3_label must be in STAGE3_LABELS")
        if self.label not in ARBITRATION_LABELS:
            raise ValueError("label must be in ARBITRATION_LABELS")
        if self.phonetic_check != PhoneticCheck.DONE and self.label != self.stage3_label:
            raise ValueError("label must equal stage3_label unless phonetic_check is done")
        return self


class ArbitrationResult(_Model):
    verdicts: tuple[WordVerdict, ...]
    coverage_candidates: tuple[CoverageCandidate, ...]
    ms_per_phoneme: tuple[NonNegFloat | None, ...]
    phonetic_check: PhoneticCheck

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(self.verdicts) != len(self.ms_per_phoneme):
            raise ValueError("verdicts and ms_per_phoneme must have the same length")
        for i, verdict in enumerate(self.verdicts):
            if verdict.unit_index != i:
                raise ValueError(f"verdicts[{i}].unit_index must equal {i}")
        return self
