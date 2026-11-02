"""Coverage guard, reason field and adaptive-overrule switch (B6a)."""

import pytest

from audio.arbitration.arbiter import arbitrate
from audio.types import (
    JoinResult,
    LaneAResult,
    PassageToken,
    PhonemeToken,
    PhoneticCheck,
    PreparedPassage,
    Unit,
)
from audio.types import WordLabel as L

STOP = ("s", "t", "ɑ", "p")  # 4 expected phonemes
IT = ("ɪ", "t")  # 2 expected phonemes
TF, TO = 0.25, 0.45


def verdict(stage3, heard, expected=STOP, hyp=None, **kw):
    passage = PreparedPassage(
        passage_hash="h",
        tokens=(PassageToken(index=0, raw="w", key="w"),),
        pronunciations={"w": (expected,)},
    )
    unit = Unit(
        surface="w",
        key="w",
        passage_index=0,
        passage_span=1,
        source_indices=(0,),
        start_ms=0,
        end_ms=500,
        align_score=1.0,
    )
    toks = tuple(
        PhonemeToken(symbol=s, start_ms=i * 10, end_ms=i * 10 + 5, confidence=0.9)
        for i, s in enumerate(heard)
    )
    join = JoinResult(
        unit_phonemes=(toks,), coverage_candidates=(), discarded=(), ms_per_phoneme=(None,)
    )
    lane_a = LaneAResult(units=(unit,), labels=(stage3,), trace=(), asr_words=())
    hypothesis = None if hyp is None else (hyp,)
    r = arbitrate(lane_a, passage, join, hypothesis, t_flag=TF, t_overrule=TO, **kw)
    return r.verdicts[0]


def test_without_a_guard_one_heard_phoneme_overrules():
    v = verdict(L.CORRECT, ("s",))
    assert v.label == L.MISPRONUNCIATION and v.phonetic_check == PhoneticCheck.DONE
    assert v.reason is None


@pytest.mark.parametrize(
    ("heard", "overruled"),
    [(("s", "t"), True), (("s",), False)],  # ratio 0.5 of 4 -> need 2: exactly at it, one below
)
def test_guard_boundary(heard, overruled):
    v = verdict(L.CORRECT, heard, coverage_guard_ratio=0.5)
    if overruled:
        assert (v.label, v.phonetic_check, v.reason) == (
            L.MISPRONUNCIATION,
            PhoneticCheck.DONE,
            None,
        )
    else:
        assert (v.label, v.phonetic_check, v.reason) == (
            L.CORRECT,
            PhoneticCheck.UNVERIFIABLE,
            "low_coverage",
        )


def test_guard_rounds_up():
    # ratio 0.34 of 4 = 1.36 -> ceil 2: one phoneme is not enough
    assert verdict(L.CORRECT, ("s",), coverage_guard_ratio=0.34).reason == "low_coverage"
    assert verdict(L.CORRECT, ("s", "t"), coverage_guard_ratio=0.34).reason is None


def test_guard_does_not_block_a_substitution_being_cleared():
    v = verdict(L.SUBSTITUTION, STOP, coverage_guard_ratio=0.67)
    assert (v.label, v.phonetic_check) == (L.CORRECT, PhoneticCheck.DONE)


def test_guard_does_not_touch_a_correct_word_that_stays_correct():
    v = verdict(L.CORRECT, STOP, coverage_guard_ratio=0.67)
    assert (v.label, v.phonetic_check, v.reason) == (L.CORRECT, PhoneticCheck.DONE, None)


def test_omission_is_unaffected_by_the_guard():
    assert verdict(L.OMISSION, (), coverage_guard_ratio=0.67).label == L.OMISSION
    v = verdict(L.OMISSION, STOP, coverage_guard_ratio=0.67)
    assert v.label == L.CORRECT  # heard the whole word: the ASR missed it


def test_reasons_for_untimed_and_nothing_heard():
    assert verdict(L.CORRECT, ()).reason == "no_heard"


def test_adaptive_overrule_switch():
    # 2 expected phonemes, one bleed phoneme heard: 0.5 > 0.45, forgiven only with the adaptive rule
    heard = ("ɪ", "t", "k")
    on = verdict(L.CORRECT, heard, expected=IT, adaptive_overrule=True)
    off = verdict(L.CORRECT, heard, expected=IT, adaptive_overrule=False)
    assert (on.label, off.label) == (L.CORRECT, L.MISPRONUNCIATION)


@pytest.mark.parametrize(
    ("max_phones", "blocked"),
    [(0, False), (1, False), (2, True), (3, True)],  # IT has 2 expected phonemes
)
def test_short_word_rule_boundary(max_phones, blocked):
    v = verdict(L.CORRECT, ("k", "k"), expected=IT, short_word_max_phones=max_phones)
    if blocked:
        assert (v.label, v.phonetic_check, v.reason) == (
            L.CORRECT,
            PhoneticCheck.UNVERIFIABLE,
            "short_word",
        )
    else:
        assert (v.label, v.phonetic_check) == (L.MISPRONUNCIATION, PhoneticCheck.DONE)


def test_short_word_rule_does_not_block_clears_or_longer_words():
    cleared = verdict(L.SUBSTITUTION, IT, expected=IT, short_word_max_phones=3)
    assert (cleared.label, cleared.phonetic_check) == (L.CORRECT, PhoneticCheck.DONE)
    long_word = verdict(L.CORRECT, ("k",), short_word_max_phones=3)  # STOP has 4 phonemes
    assert long_word.label == L.MISPRONUNCIATION
    assert verdict(L.OMISSION, (), expected=IT, short_word_max_phones=3).label == L.OMISSION
