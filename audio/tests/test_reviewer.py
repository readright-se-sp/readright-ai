import logging

import pytest

from audio.config import AudioSettings
from audio.errors import DeadlineExceeded, RecognizerError
from audio.lane_b.reviewer import PhoneticReviewer, adapt
from audio.models import AlignedWord, ReviewContext, new_row
from audio.passage import PassagePreparer
from audio.phonetics.g2p import G2PError
from audio.types import PhonemeToken, WordLabel
from tests.conftest import FakeG2P

CAT, DOG, FISH, CAN, NOT, CANNOT = (
    ("k", "æ", "t"),
    ("d", "ɑ", "g"),
    ("f", "ɪ", "ʃ"),
    ("k", "æ", "n"),
    ("n", "ɑ", "t"),
    ("k", "æ", "n", "ɑ", "t"),
)
TABLE = {
    "cat": CAT,
    "dog": DOG,
    "fish": FISH,
    "can": CAN,
    "not": NOT,
    "cannot": CANNOT,
    "zorbo": ("z", "ɔ", "b", "o"),
    "tinoy": ("t", "i", "n", "ɔ", "i"),
    "buys": ("b", "aɪ", "z"),
    "buy": ("b", "aɪ"),
}

# Tuning values pinned here so these tests stay about behaviour, not about the B6a defaults.
TEST_SETTINGS = AudioSettings(_env_file=None, COVERAGE_GUARD_RATIO=0.5, SHORT_WORD_MAX_PHONES=0)


class FakeRecogniser:
    def __init__(self, tokens=(), exc: Exception | None = None) -> None:
        self.tokens, self.exc, self.calls = tuple(tokens), exc, []

    def recognise(self, wav_path, deadline_ms):
        self.calls.append((wav_path, deadline_ms))
        if self.exc:
            raise self.exc
        return self.tokens


def heard(word_phones, start_ms, step=100):
    """PhonemeTokens for one word starting at start_ms."""
    return [
        PhonemeToken(
            symbol=s, start_ms=start_ms + i * step, end_ms=start_ms + i * step + 60, confidence=0.9
        )
        for i, s in enumerate(word_phones)
    ]


def reviewer(tokens=(), exc=None):
    rec = FakeRecogniser(tokens, exc)
    return (
        PhoneticReviewer(
            rec,
            PassagePreparer(FakeG2P(TABLE)),
            PassagePreparer(FakeG2P(TABLE), cache_size=0),
            TEST_SETTINGS,
        ),
        rec,
    )


def row(tag, word, spoken, start, end, indices=None, **kw):
    return new_row(
        tag,
        passage_indices=[] if tag == "insertion" else (indices or [0]),
        passage_words=[word] if word else None,
        spoken=[spoken] if spoken else None,
        start=start,
        end=end,
        **kw,
    )


def ctx(text, names=()):
    return ReviewContext(text, frozenset(names))


# --- adapter ---


def test_adapter_maps_tags_and_converts_seconds_to_ms():
    rows = [
        row("match", "cat", "cat", 0.0, 0.5, [0]),
        row("substitution", "dog", "fish", 0.5, 1.0, [1]),
        row("mispronunciation", "fish", "fich", 1.0, 1.5, [2]),
        row("omission", "cat", None, 1.5, 1.5, [3]),
        row("insertion", None, "uh", 1.7, 1.9),
    ]
    a = adapt(rows)
    assert a.lane_a.labels == (
        WordLabel.CORRECT,
        WordLabel.SUBSTITUTION,
        WordLabel.MISPRONUNCIATION,
        WordLabel.OMISSION,
        WordLabel.INSERTION,
    )
    assert a.row_index == (0, 1, 2, 3, 4)
    u = a.lane_a.units
    assert (u[1].start_ms, u[1].end_ms, u[1].surface) == (500, 1000, "fish")
    assert (u[1].passage_index, u[1].passage_span) == (1, 1)
    assert u[3].surface == "" and u[3].source_indices == ()  # an omission has no heard word
    assert u[4].passage_index is None and u[4].passage_span == 0


def test_adapter_untimed_rows_are_unverifiable():
    a = adapt([row("match", "cat", "cat", None, None, [0])])
    assert a.lane_a.units[0].verifiable is False


def test_adapter_multi_index_row_is_one_unit_with_a_span():
    a = adapt([row("match", "can not", "cannot", 0.0, 0.6, [4, 5])])
    (unit,) = a.lane_a.units
    assert (unit.passage_index, unit.passage_span, unit.surface) == (4, 2, "cannot")


def test_adapter_passes_other_tags_through():
    rows = [
        row("repetition", "cat", "cat cat", 0.0, 0.4, [0]),
        row("swap", "dog", "dog", 0.5, 0.9, [1], move_id=1),
        row("moved", "fish", "fish", 1.0, 1.4, [2], move_id=1),
        row("reversal", "was", "saw", 1.5, 1.9, [3]),
        row("letter_transposition", "from", "form", 2.0, 2.4, [4]),
        row("self_correction", "cat", "cot", 2.5, 2.9, [5]),
        row("filler", None, "um", 3.0, 3.2),
        row("false_start", None, "ca-", 3.3, 3.4),
        row("off_text", None, "okay", 3.5, 3.9),
        row("match", "cat", "cat", 4.0, 4.4, [6]),
    ]
    a = adapt(rows)
    assert a.row_index == (9,)


def test_adapter_skips_a_row_with_non_contiguous_indices():
    assert adapt([row("match", "a b", "a b", 0.0, 1.0, [3, 5])]).row_index == ()


# --- reviewer ---


def test_substitution_close_in_sound_becomes_correct_and_uncounted():
    rows = [row("substitution", "cat", "kat", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(CAT, 0))
    (out,) = rev.review("a.wav", rows, ctx("cat"))
    assert (out["tag"], out["counted"]) == ("match", False)
    assert out["phonetic"]["check"] == "done"
    assert out["phonetic"]["expected_phonemes"] == list(CAT)
    assert out["phonetic"]["heard_phonemes"] == list(CAT)
    assert rows[0]["tag"] == "substitution"  # input rows are not mutated
    assert "phonetic" not in rows[0]


def test_correct_word_heard_far_away_becomes_mispronunciation_and_counted():
    rows = [row("match", "cat", "cat", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(DOG, 0))
    (out,) = rev.review("a.wav", rows, ctx("cat"))
    assert (out["tag"], out["counted"]) == ("mispronunciation", True)
    assert out["phonetic"]["d_exp"] > 0.45


def test_row_count_order_and_passthrough_rows_are_kept():
    rows: list[AlignedWord] = [
        row("match", "cat", "cat", 0.0, 0.5, [0]),
        row("repetition", "cat", "cat", 0.5, 0.9, [0]),
        row("filler", None, "um", 1.0, 1.2),
        row("match", "dog", "dog", 1.3, 1.8, [1]),
    ]
    rev, _ = reviewer(heard(CAT, 0) + heard(DOG, 1300))
    out = rev.review("a.wav", rows, ctx("cat dog"))
    assert [r["tag"] for r in out] == ["match", "repetition", "filler", "match"]
    assert out[1] is rows[1] and out[2] is rows[2]
    assert [r["passage_indices"] for r in out] == [r["passage_indices"] for r in rows]
    assert "phonetic" in out[0] and "phonetic" in out[3]


def test_multi_index_row_is_judged_as_a_whole():
    rows = [row("substitution", "can not", "cannot", 0.0, 0.8, [0, 1])]
    rev, _ = reviewer(heard(CANNOT, 0))
    (out,) = rev.review("a.wav", rows, ctx("can not"))
    assert out["tag"] == "match" and out["phonetic"]["check"] == "done"


def test_untimed_row_keeps_its_tag_and_is_marked_unverifiable():
    rows = [row("substitution", "cat", "kat", None, None, [0])]
    rev, _ = reviewer(heard(CAT, 0))
    (out,) = rev.review("a.wav", rows, ctx("cat"))
    assert out["tag"] == "substitution" and out["phonetic"]["check"] == "unverifiable"


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (RecognizerError("boom"), "RecognizerError"),
        (DeadlineExceeded("late"), "deadline"),
        (RuntimeError("cuda out of memory"), "RuntimeError"),
    ],
)
def test_recogniser_failure_returns_rows_unchanged_and_is_logged(caplog, exc, reason):
    rows = [row("match", "cat", "cat", 0.0, 0.5, [0])]
    rev, _ = reviewer(exc=exc)
    with caplog.at_level(logging.WARNING):
        out = rev.review("a.wav", rows, ctx("cat"))
    assert out is rows
    assert f"phonetic_check=skipped reason={reason}" in caplog.text


def test_row_index_outside_the_passage_is_skipped(caplog):
    rev, _ = reviewer(heard(CAT, 0))
    rows = [row("match", "x", "x", 0.0, 0.5, [9])]
    with caplog.at_level(logging.WARNING):
        out = rev.review("a.wav", rows, ctx("cat"))
    assert out is rows and "phonetic_check=skipped" in caplog.text


def test_g2p_failure_is_skipped_not_raised(caplog):
    class Failing:
        def phonemize(self, words):
            raise G2PError("zzz")

    rec = FakeRecogniser(heard(CAT, 0))
    rev = PhoneticReviewer(
        rec,
        PassagePreparer(Failing()),
        PassagePreparer(Failing(), cache_size=0),
        TEST_SETTINGS,
    )
    rows = [row("match", "cat", "cat", 0.0, 0.5, [0])]
    with caplog.at_level(logging.WARNING):
        assert rev.review("a.wav", rows, ctx("cat")) is rows
    assert "reason=PassagePreparationError" in caplog.text
    assert rec.calls == []  # failed before the recogniser ran


def test_deadline_is_passed_to_the_recogniser():
    rev, rec = reviewer(heard(CAT, 0))
    context = ReviewContext("cat", frozenset(), 123)
    rev.review("a.wav", [row("match", "cat", "cat", 0.0, 0.5, [0])], context)
    assert [deadline for _, deadline in rec.calls] == [123]


def test_name_not_in_lexicon_is_never_overruled(caplog):
    rows = [row("match", "zorbo", "zorbo", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(DOG, 0))
    with caplog.at_level(logging.INFO):
        (out,) = rev.review("a.wav", rows, ctx("Zorbo", names=["zorbo"]))
    assert out["tag"] == "match" and out["counted"] is False
    assert out["phonetic"]["check"] == "unverifiable"
    assert "reason=name_not_in_lexicon" in caplog.text


def test_name_in_lexicon_is_arbitrated_normally():
    rows = [row("match", "tinoy", "tinoy", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(DOG, 0))
    (out,) = rev.review("a.wav", rows, ctx("Tinoy", names=["tinoy"]))
    assert out["tag"] == "mispronunciation" and out["phonetic"]["check"] == "done"


def test_counted_is_consistent_with_the_tag_after_any_retag():
    from audio.models import COUNTED_TAGS

    rows = [
        row("substitution", "cat", "kat", 0.0, 0.5, [0]),
        row("match", "dog", "dog", 1.0, 1.5, [1]),
    ]
    rev, _ = reviewer(heard(CAT, 0) + heard(CAT, 1000))
    for out in rev.review("a.wav", rows, ctx("cat dog")):
        assert out["counted"] == (out["tag"] in COUNTED_TAGS)


# --- ending rule and reasons (B6a) ---


def test_an_ending_row_judged_as_substitution_stays_a_mispronunciation_with_detail():
    rows = [row("substitution", "buys", "buy", 0.0, 0.5, [0])]
    rev, _ = reviewer(
        heard(("b", "aɪ"), 0)
    )  # sounds like "buy": nearer the ASR word, so substitution
    (out,) = rev.review("a.wav", rows, ctx("buys"))
    assert (out["tag"], out["detail"], out["counted"]) == ("mispronunciation", "ending", True)
    assert out["phonetic"]["check"] == "done"


def test_an_ending_row_can_still_be_cleared_to_a_match():
    rows = [row("substitution", "buys", "buy", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(("b", "aɪ", "z"), 0))  # the child said "buys"
    (out,) = rev.review("a.wav", rows, ctx("buys"))
    assert (out["tag"], out["detail"], out["counted"]) == ("match", None, False)


def test_a_substitution_that_is_not_an_ending_stays_a_substitution():
    rows = [row("substitution", "cat", "dog", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(DOG, 0))
    (out,) = rev.review("a.wav", rows, ctx("cat"))
    assert (out["tag"], out["detail"]) == ("substitution", None)


def test_unverifiable_rows_say_why():
    rows = [
        row("match", "cat", "cat", None, None, [0]),
        row("match", "dog", "dog", 1.0, 1.5, [1]),
    ]
    rev, _ = reviewer(())  # nothing heard
    untimed, silent = rev.review("a.wav", rows, ctx("cat dog"))
    assert untimed["phonetic"]["reason"] == "untimed"
    assert silent["phonetic"]["reason"] == "no_heard"


def test_a_low_coverage_overrule_is_blocked_with_a_reason():
    rows = [row("match", "fish", "fish", 0.0, 0.5, [0])]
    rev, _ = reviewer(heard(("f",), 0))  # 1 of 3 phonemes heard, guard 0.5 needs 2
    (out,) = rev.review("a.wav", rows, ctx("fish"))
    assert out["tag"] == "match"
    assert (out["phonetic"]["check"], out["phonetic"]["reason"]) == ("unverifiable", "low_coverage")


def test_default_settings_never_overrule_a_short_word():
    # B6a defaults: SHORT_WORD_MAX_PHONES=3 protects a 3-phoneme word even when little was heard
    rows = [row("match", "fish", "fish", 0.0, 0.5, [0])]
    rec = FakeRecogniser(heard(("f",), 0), None)
    rev = PhoneticReviewer(
        rec,
        PassagePreparer(FakeG2P(TABLE)),
        PassagePreparer(FakeG2P(TABLE), cache_size=0),
        AudioSettings(_env_file=None),
    )
    (out,) = rev.review("a.wav", rows, ctx("fish"))
    assert out["tag"] == "match"
    assert (out["phonetic"]["check"], out["phonetic"]["reason"]) == ("unverifiable", "short_word")
