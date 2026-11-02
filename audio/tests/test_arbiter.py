import logging

import pytest

from audio.arbitration import arbiter
from audio.arbitration.arbiter import arbitrate
from audio.types import (
    Comparison,
    CoverageCandidate,
    JoinResult,
    LaneAResult,
    PassageToken,
    PhonemeToken,
    PhoneticCheck,
    PreparedPassage,
    Unit,
)
from audio.types import WordLabel as L

TF, TO = 0.25, 0.45
EXP, HYP = (("e",),), (("h",),)
DEV = (("e", "x"),)


def passage(n=1):
    return PreparedPassage(
        passage_hash="h",
        tokens=tuple(PassageToken(index=i, raw=f"w{i}", key=f"w{i}") for i in range(n)),
        pronunciations={f"w{i}": EXP for i in range(n)},
    )


def unit(i, timed=True, insertion=False):
    return Unit(
        surface="s",
        key=f"w{i}",
        passage_index=None if insertion else i,
        passage_span=0 if insertion else 1,
        source_indices=(0,),
        start_ms=i * 1000 if timed else None,
        end_ms=i * 1000 + 500 if timed else None,
        align_score=1.0,
    )


def tok(sym="x"):
    return PhonemeToken(symbol=sym, start_ms=0, end_ms=10, confidence=0.9)


def run(labels, heard=("x",), hyp=HYP, units=None, join="auto"):
    """One unit per label; `heard` symbols are shared by every unit."""
    n = len(labels)
    units = units or tuple(unit(i) for i in range(n))
    lane_a = LaneAResult(units=units, labels=tuple(labels), trace=(), asr_words=())
    if join == "auto":
        join = JoinResult(
            unit_phonemes=(tuple(tok(s) for s in heard),) * n,
            coverage_candidates=(),
            discarded=(),
            ms_per_phoneme=(None,) * n,
        )
    h = None if hyp is None else (hyp,) * n
    return arbitrate(lane_a, passage(n), join, h, t_flag=TF, t_overrule=TO)


@pytest.fixture
def fake(monkeypatch):
    def setup(d_exp, d_hyp=None, devs=DEV):
        def compare(heard, variants):
            if variants == EXP:
                return Comparison(distance=d_exp, best_variant_index=0, deviations=devs)
            return Comparison(distance=d_hyp, best_variant_index=0, deviations=())

        monkeypatch.setattr(arbiter, "compare", compare)

    return setup


# stage3, d_exp, d_hyp, deviations, heard, expected final
ROWS = [
    (L.CORRECT, 0.45, None, DEV, ("x",), L.CORRECT),
    (L.CORRECT, 0.46, None, DEV, ("x",), L.MISPRONUNCIATION),
    (L.CORRECT, 0.9, None, (), ("x",), L.CORRECT),
    (L.SUBSTITUTION, 0.25, 0.9, DEV, ("x",), L.CORRECT),
    (L.MISPRONUNCIATION, 0.25, 0.9, DEV, ("x",), L.CORRECT),
    (L.SUBSTITUTION, 0.6, 0.3, DEV, ("x",), L.SUBSTITUTION),
    (L.MISPRONUNCIATION, 0.6, 0.3, DEV, ("x",), L.SUBSTITUTION),
    (L.SUBSTITUTION, 0.6, 0.7, DEV, ("x",), L.MISPRONUNCIATION),
    (L.MISPRONUNCIATION, 0.6, 0.6, DEV, ("x",), L.MISPRONUNCIATION),
    (L.OMISSION, 1.0, None, DEV, (), L.OMISSION),
    (L.OMISSION, 0.25, None, (), ("x",), L.CORRECT),
    (L.OMISSION, 0.6, None, DEV, ("x",), L.OMISSION),
]


@pytest.mark.parametrize(("stage3", "d_exp", "d_hyp", "devs", "heard", "final"), ROWS)
def test_label_table(fake, stage3, d_exp, d_hyp, devs, heard, final):
    fake(d_exp, d_hyp, devs)
    v = run([stage3], heard=heard).verdicts[0]
    assert v.label == final
    assert v.phonetic_check == PhoneticCheck.DONE
    assert v.distance_expected == d_exp
    if stage3 in (L.SUBSTITUTION, L.MISPRONUNCIATION):
        assert v.distance_hypothesis == d_hyp


def test_insertion_passes_through():
    v = run([L.INSERTION], units=(unit(0, insertion=True),)).verdicts[0]
    assert v.label == L.INSERTION and v.distance_expected is None


def test_distances_recorded_on_unchanged_label(fake):
    fake(0.1)
    v = run([L.CORRECT]).verdicts[0]
    assert v.label == L.CORRECT and v.distance_expected == 0.1 and v.best_variant_index == 0


def test_degraded_path(caplog):
    with caplog.at_level(logging.WARNING, logger=arbiter.logger.name):
        r = run([L.CORRECT, L.OMISSION, L.SUBSTITUTION], join=None)
    assert r.phonetic_check == PhoneticCheck.SKIPPED
    assert [v.label for v in r.verdicts] == [L.CORRECT, L.OMISSION, L.SUBSTITUTION]
    assert all(v.phonetic_check == PhoneticCheck.SKIPPED for v in r.verdicts)
    assert r.coverage_candidates == () and r.ms_per_phoneme == (None,) * 3
    assert [x.message for x in caplog.records].count("phonetic_check=skipped") == 1


def test_unverifiable_unit_keeps_label(fake):
    fake(0.9, 0.1)
    v = run([L.SUBSTITUTION], units=(unit(0, timed=False),)).verdicts[0]
    assert v.label == L.SUBSTITUTION and v.phonetic_check == PhoneticCheck.UNVERIFIABLE
    assert v.distance_expected is None


def test_timed_correct_without_heard_is_unverifiable():
    v = run([L.CORRECT], heard=()).verdicts[0]
    assert v.label == L.CORRECT and v.phonetic_check == PhoneticCheck.UNVERIFIABLE


def test_hypothesis_none_or_empty_keeps_sub(fake):
    fake(0.6, 0.3)
    for hyp in (None, ()):
        r = run([L.SUBSTITUTION, L.MISPRONUNCIATION, L.CORRECT], hyp=hyp)
        assert all(v.distance_hypothesis is None for v in r.verdicts)
        assert [v.label for v in r.verdicts][:2] == [L.SUBSTITUTION, L.MISPRONUNCIATION]


def test_hypothesis_never_recorded_for_correct(fake):
    fake(0.1, 0.2)
    assert run([L.CORRECT]).verdicts[0].distance_hypothesis is None


def test_join_passthrough():
    cand = CoverageCandidate(start_ms=0, end_ms=200, phonemes=(tok(),), after_passage_index=None)
    j = JoinResult(
        unit_phonemes=((tok("e"),),),
        coverage_candidates=(cand,),
        discarded=(),
        ms_per_phoneme=(42.0,),
    )
    r = run([L.CORRECT], join=j)
    assert r.coverage_candidates == (cand,) and r.ms_per_phoneme == (42.0,)
    assert r.phonetic_check == PhoneticCheck.DONE
    assert r.verdicts[0].distance_expected == 0  # real compare


def test_length_mismatch_raises():
    j = JoinResult(unit_phonemes=(), coverage_candidates=(), discarded=(), ms_per_phoneme=())
    with pytest.raises(ValueError):
        run([L.CORRECT], join=j)
    lane_a = LaneAResult(units=(unit(0),), labels=(L.CORRECT,), trace=(), asr_words=())
    with pytest.raises(ValueError):
        arbitrate(lane_a, passage(1), None, (HYP, HYP), t_flag=TF, t_overrule=TO)


@pytest.mark.parametrize(
    ("stage3", "d_exp", "logged"),
    [
        (L.CORRECT, 0.40, True),
        (L.CORRECT, 0.50, True),
        (L.CORRECT, 0.30, False),
        (L.CORRECT, 0.51, False),
        (L.SUBSTITUTION, 0.30, True),
        (L.SUBSTITUTION, 0.20, True),
        (L.SUBSTITUTION, 0.45, False),
    ],
)
def test_near_threshold_log(fake, caplog, stage3, d_exp, logged):
    fake(d_exp, 0.9)
    with caplog.at_level(logging.INFO, logger=arbiter.logger.name):
        run([stage3])
    assert any("near_threshold" in r.message for r in caplog.records) is logged
