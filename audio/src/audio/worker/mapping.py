"""AudioResult -> worker response (contracts/worker-api.md). Pure: no I/O, no models.

One miscue per counted error, so the list agrees with ``reading_score``. The tag table below is
the only place Lane A tags meet the BE ``MiscueType`` values; every tag in COUNTED_TAGS has an
entry. ``self_correction`` is never counted but is sent. match, filler, false_start and off_text
are never sent.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from audio.alignment.text import normalize
from audio.models import AlignedWord, AudioResult, Tag
from audio.phonetics.g2p import G2P, G2PError
from audio.phonetics.ipa import normalise_segments
from audio.worker.schemas import AudioWorkerResult, WorkerMiscue

logger = logging.getLogger(__name__)

TAG_TO_MISCUE_TYPE: dict[Tag, str] = {
    "mispronunciation": "mispronunciation",
    "substitution": "substitution",
    "omission": "omission",
    "insertion": "insertion",
    "repetition": "repetition",
    "swap": "transposition",
    "moved": "transposition",
    "letter_transposition": "transposition",
    "reversal": "reversal",
    "self_correction": "self-correction",
}

_NOISE: frozenset[Tag] = frozenset({"filler", "false_start"})
_MOVES: frozenset[Tag] = frozenset({"swap", "moved"})


@dataclass
class _Group:
    """The rows that make up one miscue: indices into the result's rows."""

    tag: Tag
    rows: list[int] = field(default_factory=lambda: [])


def _groups(rows: Sequence[AlignedWord]) -> list[_Group]:
    by_move: dict[int, list[int]] = {}
    for i, row in enumerate(rows):
        if row["tag"] in _MOVES and row["move_id"] is not None:
            by_move.setdefault(row["move_id"], []).append(i)

    groups: list[_Group] = []
    run: _Group | None = None  # the open insertion run
    for i, row in enumerate(rows):
        tag = row["tag"]
        if tag == "insertion":
            if row["counted"]:
                run = _Group(tag, [i])
                groups.append(run)
            elif run is not None:
                run.rows.append(i)
            continue
        if tag not in _NOISE:
            run = None
        if tag in _MOVES:
            if row["counted"]:
                move_id = row["move_id"]
                members = by_move[move_id] if move_id is not None else [i]
                groups.append(_Group(tag, members))
        elif tag in TAG_TO_MISCUE_TYPE and (row["counted"] or tag == "self_correction"):
            groups.append(_Group(tag, [i]))
    return groups


def _g2p_phrase(phrase: str, g2p: G2P) -> list[str]:
    """Best espeak pronunciation of each word, joined. A word espeak can't read adds nothing."""
    out: list[str] = []
    cache: dict[str, list[str]] = {}
    for word in (w for part in phrase.split() for w in normalize(part)):
        if word not in cache:
            try:
                cache[word] = list(g2p.phonemize([word])[0])
            except G2PError:
                logger.warning("no G2P pronunciation for a word; leaving it out")
                cache[word] = []
        out.extend(cache[word])
    return out


def _clean(phones: Sequence[str]) -> list[str]:
    """IPA segments with stress and other marks stripped (ipa.normalise_segments)."""
    return list(normalise_segments(phones))


def _actual_phoneme(members: Sequence[AlignedWord], phrase: str, g2p: G2P) -> list[str]:
    if not phrase:
        return []
    evidence = [r.get("phonetic") for r in members if r["passage_indices"]]
    if evidence and all(ev is not None and ev["expected_phonemes"] for ev in evidence):
        return _clean([p for ev in evidence if ev is not None for p in ev["expected_phonemes"]])
    return _clean(_g2p_phrase(phrase, g2p))


def _spoken_phoneme(members: Sequence[AlignedWord], phrase: str, g2p: G2P) -> list[str]:
    if not phrase:
        return []
    evidence = [r.get("phonetic") for r in members if r["spoken_word"]]
    if evidence and all(ev is not None and ev["check"] == "done" for ev in evidence):
        return _clean([p for ev in evidence if ev is not None for p in ev["heard_phonemes"]])
    return _clean(_g2p_phrase(phrase, g2p))


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _timed(row: AlignedWord) -> bool:
    return row["start"] is not None and row["end"] is not None


def _span(rows: Sequence[AlignedWord], members: list[int], tag: Tag) -> tuple[int, int]:
    """(start_ms, end_ms): the members' own times, or for an omission / untimed rows the gap.

    The gap runs from the end of the previous timed row to the start of the next one. If they
    touch or overlap, or only one side exists, it is zero-length at the side that exists; with
    neither side it is 0-0. Omissions don't count as neighbours (their times are the gap itself).
    """
    own = [rows[i] for i in members if _timed(rows[i])]
    if tag != "omission" and own:
        start = min(r["start"] for r in own if r["start"] is not None)
        end = max(r["end"] for r in own if r["end"] is not None)
        return _ms(start), max(_ms(start), _ms(end))

    def usable(r: AlignedWord) -> bool:
        return r["tag"] != "omission" and _timed(r)

    before = next((r["end"] for r in reversed(rows[: members[0]]) if usable(r)), None)
    after = next((r["start"] for r in rows[members[-1] + 1 :] if usable(r)), None)
    if before is not None and after is not None and after > before:
        return _ms(before), _ms(after)
    edge = before if before is not None else after
    if edge is None:
        return 0, 0
    return _ms(edge), _ms(edge)


def to_worker_result(
    job_id: UUID, result: AudioResult, passage_tokens: Sequence[str], g2p: G2P
) -> AudioWorkerResult:
    """Maps one AudioResult. ``passage_tokens`` is ``tokenize_passage(passage_content)``."""
    rows: list[AlignedWord] = result["words"]
    score = result["score"]
    built: list[tuple[tuple[int, int], WorkerMiscue]] = []
    for group in _groups(rows):
        members = sorted(group.rows)
        member_rows = [rows[i] for i in members]
        indices = sorted({i for r in member_rows for i in r["passage_indices"]})
        actual = " ".join(passage_tokens[i] for i in indices if 0 <= i < len(passage_tokens))
        spoken = " ".join(r["spoken_word"] for r in member_rows if r["spoken_word"])
        start, end = _span(rows, members, group.tag)
        miscue = WorkerMiscue(
            actual_phrase=actual,
            spoken_phrase=spoken,
            actual_phoneme=_actual_phoneme(member_rows, actual, g2p),
            spoken_phoneme=_spoken_phoneme(member_rows, spoken, g2p),
            start_ms=start,
            end_ms=end,
            miscue_type=TAG_TO_MISCUE_TYPE[group.tag],
        )
        built.append(((start, members[0]), miscue))
    built.sort(key=lambda item: item[0])

    pct = score["word_recognition_pct"]
    wpm = score["wpm"]
    return AudioWorkerResult(
        job_id=job_id,
        reading_score=min(100.0, max(0.0, pct)) if math.isfinite(pct) else 0.0,
        wpm=wpm if wpm is not None and math.isfinite(wpm) and wpm >= 0 else 0.0,
        is_word_by_word=False,
        is_disregard_punctuation=False,
        is_monotonous=False,
        is_hardly_audible=False,
        miscues=[m for _, m in built],
    )
