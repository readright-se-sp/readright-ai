import math
from typing import Any
from uuid import uuid4

import pytest

from audio.models import COUNTED_TAGS, AlignedWord, AudioResult, ReadingScore, Tag, new_row
from audio.worker.mapping import TAG_TO_MISCUE_TYPE, to_worker_result
from tests.conftest import FakeG2P

PASSAGE = ["the", "cat", "sat", "on", "the", "mat"]


def result(rows: list[AlignedWord], *, pct: float = 80.0, wpm: float | None = 60.0) -> AudioResult:
    errors = sum(1 for r in rows if r["counted"])
    score = ReadingScore(
        counts={},  # type: ignore[typeddict-item]
        total_words=len(PASSAGE),
        error_count=errors,
        word_recognition_pct=pct,
        wpm=wpm,
        reading_level="Frustration",
    )
    return AudioResult(version="6", score=score, words=rows, transcript=[])


def run(rows: list[AlignedWord], **kw: Any):
    return to_worker_result(uuid4(), result(rows, **kw), PASSAGE, FakeG2P())


def match(i: int, start: float, end: float) -> AlignedWord:
    return new_row(
        "match",
        passage_indices=[i],
        passage_words=[PASSAGE[i]],
        spoken=[PASSAGE[i]],
        start=start,
        end=end,
    )


def sub(i: int, spoken: str, start: float | None = None, end: float | None = None) -> AlignedWord:
    return new_row(
        "substitution",
        passage_indices=[i],
        passage_words=[PASSAGE[i]],
        spoken=[spoken],
        start=start,
        end=end,
    )


def test_every_counted_tag_has_a_miscue_type() -> None:
    assert COUNTED_TAGS <= set(TAG_TO_MISCUE_TYPE)
    assert "self_correction" in TAG_TO_MISCUE_TYPE


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("mispronunciation", "mispronunciation"),
        ("substitution", "substitution"),
        ("omission", "omission"),
        ("insertion", "insertion"),
        ("repetition", "repetition"),
        ("swap", "transposition"),
        ("moved", "transposition"),
        ("letter_transposition", "transposition"),
        ("reversal", "reversal"),
        ("self_correction", "self-correction"),
    ],
)
def test_tag_table(tag: Tag, expected: str) -> None:
    assert TAG_TO_MISCUE_TYPE[tag] == expected
    no_passage = tag in ("insertion", "repetition")
    row = new_row(
        tag,
        passage_indices=None if no_passage else [1],
        passage_words=None if no_passage else ["cat"],
        spoken=None if tag == "omission" else ["cap"],
        start=1.0,
        end=1.4,
        move_id=1 if tag in ("swap", "moved") else None,
    )
    out = run([row])
    assert [m.miscue_type for m in out.miscues] == [expected]


def test_never_sent_tags() -> None:
    rows = [
        match(0, 0.0, 0.2),
        new_row("filler", spoken=["um"], start=0.3, end=0.4),
        new_row("false_start", spoken=["ca"], start=0.5, end=0.6),
        new_row("off_text", spoken=["hello"], start=0.7, end=0.8),
    ]
    assert run(rows).miscues == []


def test_self_correction_is_sent_but_not_counted() -> None:
    row = new_row(
        "self_correction",
        passage_indices=[1],
        passage_words=["cat"],
        spoken=["cat"],
        start=1.0,
        end=1.3,
    )
    out = run([row])
    assert [m.miscue_type for m in out.miscues] == ["self-correction"]
    assert out.miscues[0].actual_phrase == "cat"


def test_swap_halves_are_one_miscue() -> None:
    rows = [
        new_row("swap", passage_indices=[1], passage_words=["cat"], move_id=7),
        new_row("swap", spoken=["sat"], start=1.0, end=1.3, move_id=7, counted=False),
        new_row("swap", passage_indices=[2], passage_words=["sat"], move_id=7, counted=False),
        new_row("swap", spoken=["cat"], start=1.4, end=1.7, move_id=7, counted=False),
    ]
    out = run(rows)
    assert len(out.miscues) == 1
    m = out.miscues[0]
    assert (m.actual_phrase, m.spoken_phrase) == ("cat sat", "sat cat")
    assert m.miscue_type == "transposition"
    assert (m.start_ms, m.end_ms) == (1000, 1700)


def test_moved_pair_is_one_miscue() -> None:
    rows = [
        new_row("moved", passage_indices=[3], passage_words=["on"], move_id=2),
        match(4, 0.2, 0.4),
        new_row("moved", spoken=["on"], start=0.5, end=0.7, move_id=2, counted=False),
    ]
    out = run(rows)
    assert len(out.miscues) == 1
    assert (out.miscues[0].start_ms, out.miscues[0].end_ms) == (500, 700)


def test_insertion_run_is_one_miscue() -> None:
    rows = [
        match(0, 0.0, 0.2),
        new_row("insertion", spoken=["very"], start=0.3, end=0.5),
        new_row("filler", spoken=["um"], start=0.5, end=0.6),
        new_row("insertion", spoken=["big"], start=0.6, end=0.8, counted=False),
        new_row("insertion", spoken=["old"], start=0.8, end=1.0, counted=False),
        match(1, 1.1, 1.3),
        new_row("insertion", spoken=["so"], start=1.4, end=1.5),  # a new run
    ]
    out = run(rows)
    assert [m.spoken_phrase for m in out.miscues] == ["very big old", "so"]
    assert (out.miscues[0].start_ms, out.miscues[0].end_ms) == (300, 1000)
    assert out.miscues[0].actual_phrase == ""
    assert out.miscues[0].actual_phoneme == []


def test_miscue_count_matches_error_count() -> None:
    rows = [
        sub(0, "a", 0.0, 0.2),
        new_row("insertion", spoken=["x"], start=0.3, end=0.4),
        new_row("insertion", spoken=["y"], start=0.4, end=0.5, counted=False),
        new_row("omission", passage_indices=[2], passage_words=["sat"]),
        new_row("moved", passage_indices=[3], passage_words=["on"], move_id=1),
        new_row("moved", spoken=["on"], start=0.9, end=1.0, move_id=1, counted=False),
        new_row("repetition", spoken=["the", "the"], start=1.1, end=1.4),
    ]
    out = run(rows)
    assert len(out.miscues) == result(rows)["score"]["error_count"] == 5


def test_multi_index_row() -> None:
    row = new_row(
        "substitution",
        passage_indices=[1, 2],
        passage_words=["cat", "sat"],
        spoken=["cassat"],
        start=1.0,
        end=1.5,
    )
    m = run([row]).miscues[0]
    assert m.actual_phrase == "cat sat"
    assert m.spoken_phrase == "cassat"


def omission_rows(before: tuple[float, float] | None, after: tuple[float, float] | None):
    rows: list[AlignedWord] = []
    if before:
        rows.append(match(0, *before))
    # place_omissions gives the omission its own times; the mapper ignores them
    rows.append(new_row("omission", passage_indices=[1], passage_words=["cat"], start=9.0, end=9.5))
    if after:
        rows.append(match(2, *after))
    return rows


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ((0.0, 1.0), (1.5, 2.0), (1000, 1500)),  # between neighbours
        ((0.0, 1.0), (1.0, 2.0), (1000, 1000)),  # touching
        ((0.0, 1.2), (1.0, 2.0), (1200, 1200)),  # overlapping
        (None, (1.5, 2.0), (1500, 1500)),  # at the start
        ((0.0, 1.0), None, (1000, 1000)),  # at the end
        (None, None, (0, 0)),  # no timed rows
    ],
)
def test_omission_gap(before: Any, after: Any, expected: tuple[int, int]) -> None:
    m = run(omission_rows(before, after)).miscues[0]
    assert (m.start_ms, m.end_ms) == expected
    assert m.spoken_phrase == "" and m.spoken_phoneme == []


def test_two_omissions_in_a_row_share_the_whole_gap() -> None:
    rows = [
        match(0, 0.0, 1.0),
        new_row("omission", passage_indices=[1], passage_words=["cat"], start=1.0, end=1.2),
        new_row("omission", passage_indices=[2], passage_words=["sat"], start=1.2, end=2.0),
        match(3, 2.0, 2.5),
    ]
    out = run(rows)
    assert [(m.start_ms, m.end_ms) for m in out.miscues] == [(1000, 2000), (1000, 2000)]


def test_untimed_substitution_uses_the_gap() -> None:
    rows = [match(0, 0.0, 1.0), sub(1, "cap"), match(2, 1.4, 2.0)]
    m = run(rows).miscues[0]
    assert (m.start_ms, m.end_ms) == (1000, 1400)


def test_ordered_by_start() -> None:
    rows = [sub(1, "cap", 2.0, 2.2), sub(0, "a", 1.0, 1.2)]
    assert [m.start_ms for m in run(rows).miscues] == [1000, 2000]


def with_evidence(
    check: str = "done",
    expected: tuple[str, ...] = ("k", "æ", "t"),
    heard: tuple[str, ...] = ("k", "æ", "p"),
) -> AlignedWord:
    row = sub(1, "cap", 1.0, 1.4)
    row["phonetic"] = {  # type: ignore[typeddict-item]
        "expected_phonemes": list(expected),
        "heard_phonemes": list(heard),
        "d_exp": 0.4,
        "d_hyp": 0.0,
        "check": check,
    }
    return row


def test_phonemes_from_lane_b_when_done() -> None:
    m = run([with_evidence()]).miscues[0]
    assert m.actual_phoneme == ["k", "æ", "t"]
    assert m.spoken_phoneme == ["k", "æ", "p"]


def test_phonemes_fall_back_to_g2p_without_evidence() -> None:
    m = run([sub(1, "cap", 1.0, 1.4)]).miscues[0]
    assert m.actual_phoneme == ["c", "a", "t"]  # FakeG2P: one segment per letter
    assert m.spoken_phoneme == ["c", "a", "p"]


def test_heard_phonemes_only_when_check_done() -> None:
    m = run([with_evidence(check="skipped")]).miscues[0]
    assert m.actual_phoneme == ["k", "æ", "t"]  # expected evidence is still used
    assert m.spoken_phoneme == ["c", "a", "p"]  # heard is not trusted: G2P of the spoken word


def test_stress_marks_are_stripped() -> None:
    m = run([with_evidence(expected=("ˈk", "æ", "t"), heard=("k", "ˌæ", "p"))]).miscues[0]
    assert m.actual_phoneme == ["k", "æ", "t"]
    assert m.spoken_phoneme == ["k", "æ", "p"]


def test_wpm_none_and_score_clamped() -> None:
    out = run([match(0, 0, 1)], pct=104.2, wpm=None)
    assert out.wpm == 0.0
    assert out.reading_score == 100.0
    assert run([], pct=-3.0).reading_score == 0.0
    assert math.isfinite(run([], pct=float("nan")).reading_score)


def test_flags_are_false() -> None:
    out = run([])
    flags = [
        out.is_word_by_word,
        out.is_disregard_punctuation,
        out.is_monotonous,
        out.is_hardly_audible,
    ]
    assert not any(flags)
