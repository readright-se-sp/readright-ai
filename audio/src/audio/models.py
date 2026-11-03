from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, TypedDict

# Base tags (alignment): match, substitution, omission, insertion, swap, repetition, moved.
# Derived tags (tagging/): mispronunciation, reversal, letter_transposition, self_correction.
# Noise: filler, false_start, off_text (talk before or after the reading).
# Phil-IRI transposition = swap + moved (words) + letter_transposition.
Tag: TypeAlias = Literal[
    "match",
    "mispronunciation",
    "substitution",
    "omission",
    "insertion",
    "repetition",
    "swap",
    "moved",
    "reversal",
    "letter_transposition",
    "self_correction",
    "filler",
    "false_start",
    "off_text",
]
EventDetail: TypeAlias = Literal["ending", "miscorrection", "proper_noun"]
ReadingLevel: TypeAlias = Literal["Independent", "Instructional", "Frustration"]
ColumnOp: TypeAlias = Literal["match", "sub", "del", "ins", "rep", "swp", "mid", "off"]

# Bump with any change to the response shape (contracts/audio-service.md).
CONTRACT_VERSION = "7"

# Phil-IRI 2018 counted miscues. Everything else is recorded but never scored.
COUNTED_TAGS: frozenset[Tag] = frozenset(
    {
        "mispronunciation",
        "substitution",
        "omission",
        "insertion",
        "repetition",
        "swap",
        "moved",
        "reversal",
        "letter_transposition",
    }
)


class SpokenWord(TypedDict):
    """One ASR word. Timing/score None when the aligner couldn't place it."""

    text: str
    start: float | None
    end: float | None
    score: float | None


PhoneticCheckState: TypeAlias = Literal["done", "unverifiable", "skipped"]


class PhoneticEvidence(TypedDict):
    """What Lane B heard for one row (set by lane_b/reviewer.py; absent on rows Lane B did not judge).

    Phonemes are IPA segments after audio.phonetics.ipa normalisation. expected_phonemes is the best
    matching accepted pronunciation (empty for an insertion). d_exp / d_hyp are the weighted distances
    to the passage word and to the word the ASR wrote (None when not computed). Only check == "done"
    means Lane B decided the tag; the post-pass leaves such rows to Lane B.
    """

    expected_phonemes: list[str]
    heard_phonemes: list[str]
    d_exp: float | None
    d_hyp: float | None
    check: PhoneticCheckState
    reason: NotRequired[str]  # why check is not "done": untimed, no_heard, low_coverage, short_word, name_not_in_lexicon


class AlignedWord(TypedDict):
    """One output row: (passage_word, spoken_word, start, end, tag, move_id) + bookkeeping.

    passage_word None = only spoken (insertion, repetition, filler, heard half of a move).
    spoken_word None = skipped (omission, passage half of a move). An omission's start/end is where the
    word should have been read (alignment/detection.place_omissions); the passage half of a move is untimed.
    swap/moved: one row per word, all rows of one move share move_id, only the first is counted.
    A repeated phrase is one row, words space-joined. Every passage index is in exactly one row.
    """

    passage_word: str | None
    spoken_word: str | None
    start: float | None
    end: float | None
    tag: Tag
    counted: bool
    passage_indices: list[int]
    attempts: list[str]
    detail: EventDetail | None
    move_id: int | None
    phonetic: NotRequired[PhoneticEvidence]


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """Per-request facts a row reviewer (Lane B) needs besides the audio and the rows.

    passage_text is the passage as given (the reviewer needs the written forms for G2P), proper_nouns
    the normalised name words Lane A used, deadline_ms the absolute UTC epoch deadline in ms (None = none).
    """

    passage_text: str
    proper_nouns: frozenset[str]
    deadline_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Token:
    """Normalised heard word. `id` = order in the cleaned transcript, never renumbered."""

    id: int
    norm: str
    raw: str
    start: float | None
    end: float | None


@dataclass(frozen=True, slots=True)
class Column:
    """One NW column. del = passage word not read, ins = extra heard word.

    rep = heard word re-reading passage word p, which an earlier column already covers.
    swp = one half of a transposition (heard word read passage word p out of order).
    mid = the word left in place between the halves of an exchange ('ripe fish fresh').
    off = extra heard word in talk before/after the reading (alignment/off_text.py), not a miscue.
    """

    op: ColumnOp
    p: int | None
    h: Token | None


@dataclass(frozen=True, slots=True)
class Swap:
    """Passage words p < q read in each other's places. heard = (reading of q, reading of p), as spoken.

    mids = words between them, read in place ('fresh fish ripe' -> 'ripe fish fresh'); empty for neighbours.
    tries = (passage index, earlier different readings) from a copy said before the swap ('happy wos happy was').
    """

    p: int
    q: int
    heard: tuple[Token, Token]
    mids: tuple[Column, ...] = ()
    tries: tuple[tuple[int, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class Repeat:
    """A re-read stretch, folded from NW rep columns. Last reading of each word is judged.

    heard = earlier copies read the same (the repetition row; empty if every word changed).
    A repetition is contiguous: if words were added between the copies ('I… and I will'),
    the earlier copy and the added words are insertions instead (ins columns, first in columns).
    columns = those insertions, then the stretch with each word's last reading.
    tries[k] = earlier, different readings of columns[k] ("ben rans ben runs" -> runs gets ("rans",)).
    """

    heard: tuple[Token, ...]
    columns: tuple[Column, ...]
    tries: tuple[tuple[str, ...], ...]


AlignedItem: TypeAlias = Column | Swap | Repeat


@dataclass(frozen=True, slots=True)
class SideEvent:
    """Row found before NW (repeat, filler, false start). Placed before token `anchor`."""

    anchor: int
    row: AlignedWord


class MiscueCounts(TypedDict):
    """Per-tag counts. swap/moved count moves, not rows. self_correction/filler/false_start aren't errors."""

    match: int
    mispronunciation: int
    substitution: int
    omission: int
    insertion: int
    repetition: int
    swap: int
    moved: int
    reversal: int
    letter_transposition: int
    self_correction: int
    filler: int
    false_start: int
    off_text: int


class ReadingScore(TypedDict):
    """Word-reading score for one recording."""

    counts: MiscueCounts
    total_words: int
    error_count: int
    word_recognition_pct: float
    wpm: float | None
    reading_level: ReadingLevel


class AudioResult(TypedDict):
    """What the audio pipeline returns for one recording. Shape documented in contracts/audio-service.md."""

    version: str
    score: ReadingScore
    words: list[AlignedWord]
    transcript: list[SpokenWord]


def new_row(
    tag: Tag,
    *,
    passage_indices: list[int] | None = None,
    passage_words: list[str] | None = None,
    spoken: list[str] | None = None,
    attempts: list[str] | None = None,
    detail: EventDetail | None = None,
    start: float | None = None,
    end: float | None = None,
    move_id: int | None = None,
    counted: bool | None = None,
) -> AlignedWord:
    """Builds an AlignedWord; `counted` defaults from the tag, word lists space-joined."""
    return AlignedWord(
        passage_word=" ".join(passage_words) if passage_words else None,
        spoken_word=" ".join(spoken) if spoken else None,
        start=start,
        end=end,
        tag=tag,
        counted=tag in COUNTED_TAGS if counted is None else counted,
        passage_indices=passage_indices or [],
        attempts=attempts or [],
        detail=detail,
        move_id=move_id,
    )


def retag(row: AlignedWord, tag: Tag, detail: EventDetail | None = None) -> AlignedWord:
    """Copy of `row` with a new tag (keeps `counted` in sync)."""
    out = row.copy()
    out["tag"] = tag
    out["counted"] = tag in COUNTED_TAGS
    out["detail"] = detail
    return out
