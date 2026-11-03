import pytest

from audio.alignment.text import tokenize_passage
from audio.config import AudioSettings
from audio.flags.fluency import (
    disregard_punctuation,
    measure_gaps,
    punctuation_boundaries,
    word_by_word,
)
from audio.models import AlignedWord, Tag, new_row

S = AudioSettings(_env_file=None)


def read_rows(n: int, gap: float, *, ends: dict[int, float] | None = None) -> list[AlignedWord]:
    """n matched words of 0.3 s each; ``gap`` seconds after each, ``ends`` overrides by index."""
    rows: list[AlignedWord] = []
    t = 0.0
    for i in range(n):
        rows.append(
            new_row(
                "match",
                passage_indices=[i],
                passage_words=["w"],
                spoken=["w"],
                start=t,
                end=t + 0.3,
            )
        )
        t += 0.3 + (ends or {}).get(i, gap)
    return rows


def sentence_ends(n: int, every: int) -> dict[int, str]:
    return {i: "sentence" for i in range(every - 1, n, every)}


def test_boundaries_follow_tokenize_passage_indices() -> None:
    text = 'Hello, world. Well-known 21 Tinoy\'s, "ok!" end'
    tokens = tokenize_passage(text)
    b = punctuation_boundaries(text)
    assert tokens[:5] == ["hello", "world", "well", "known", "twenty"]
    assert b[0] == "comma"
    assert b[1] == "sentence"
    assert tokens[tokens.index("tinoys")] == "tinoys"
    assert b[tokens.index("tinoys")] == "comma"
    assert b[tokens.index("ok")] == "sentence"  # the closing quote is skipped
    assert max(b) < len(tokens) - 1  # nothing after the last word


def test_fluent_reading_is_not_word_by_word() -> None:
    gaps = measure_gaps(read_rows(30, 0.02), {})
    out = word_by_word(gaps, S)
    assert not out.flag
    assert out.metrics["wbw_pause_share"] == 0


def test_every_gap_400ms_is_word_by_word() -> None:
    out = word_by_word(measure_gaps(read_rows(30, 0.4), {}), S)
    assert out.flag
    assert out.metrics["wbw_pause_share"] == 1


def test_gaps_next_to_miscues_are_skipped() -> None:
    rows = read_rows(30, 0.02)
    # a long hesitation around an omission and an insertion must not count
    rows.insert(5, new_row("omission", passage_indices=[99], passage_words=["x"]))
    rows.insert(
        10, new_row("insertion", spoken=["uh"], start=rows[9]["end"], end=rows[9]["end"] + 1.0)
    )
    out = word_by_word(measure_gaps(rows, {}), S)
    assert out.metrics["wbw_gaps"] == 29 - 2  # each miscue splits one neighbour pair
    assert not out.flag


def test_too_few_gaps_is_insufficient() -> None:
    out = word_by_word(measure_gaps(read_rows(8, 0.5), {}), S)
    assert not out.flag
    assert out.metrics["wbw_reason"] == "insufficient"


def test_punctuation_gaps_split_by_kind() -> None:
    b = {4: "sentence", 9: "comma"}
    gaps = measure_gaps(read_rows(12, 0.02, ends={4: 0.6, 9: 0.3}), b)
    assert len(gaps.sentence) == 1 and len(gaps.comma) == 1 and len(gaps.within) == 9
    assert gaps.sentence[0] == pytest.approx(0.6)


def test_pair_across_a_missing_index_is_skipped() -> None:
    rows = read_rows(6, 0.02)
    del rows[3]  # passage index 3 is not here: 2 -> 4 is not a neighbour pair
    assert len(measure_gaps(rows, {}).within) == 3


def test_repetition_rows_are_skipped() -> None:
    rows = read_rows(6, 0.02)
    rows.insert(3, new_row("repetition", spoken=["w"], start=0.9, end=1.0))
    assert len(measure_gaps(rows, {}).within) == 4  # the row splits one of the 5 pairs


def punct_case(within: float, at_stops: float) -> tuple[bool, dict[str, float | int | str | None]]:
    n, every = 40, 8
    b = {i: "sentence" for i in sentence_ends(n, every)}
    ends = {i: at_stops for i in b}
    out = disregard_punctuation(measure_gaps(read_rows(n, within, ends=ends), b), S)
    return out.flag, out.metrics


def test_word_by_word_reader_with_no_extra_pause_disregards_punctuation() -> None:
    flag, m = punct_case(within=0.4, at_stops=0.45)
    assert flag  # 450 ms is long, but under 1.5 x the 400 ms median
    assert m["punct_sentence_honoured_share"] == 0


def test_reader_who_pauses_at_full_stops_observes_punctuation() -> None:
    flag, m = punct_case(within=0.05, at_stops=0.6)
    assert not flag
    assert m["punct_sentence_honoured_share"] == 1


def test_fluent_reader_who_runs_through_stops_disregards() -> None:
    flag, _ = punct_case(within=0.05, at_stops=0.05)
    assert flag


def test_too_few_sentence_ends_is_insufficient() -> None:
    b = {4: "sentence"}
    out = disregard_punctuation(measure_gaps(read_rows(12, 0.02, ends={4: 0.0}), b), S)
    assert not out.flag
    assert out.metrics["punct_reason"] == "insufficient"


def test_tags_outside_read_set_are_never_measured() -> None:
    tags: list[Tag] = ["omission", "insertion", "filler", "false_start", "off_text", "swap"]
    rows = [
        new_row(t, passage_indices=[i], start=float(i), end=i + 0.2) for i, t in enumerate(tags)
    ]
    gaps = measure_gaps(rows, {})
    assert not (gaps.within or gaps.sentence or gaps.comma)
