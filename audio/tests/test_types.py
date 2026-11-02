import pytest
from conftest import load_fixture
from pydantic import ValidationError

from audio.types import (
    ARBITRATION_LABELS,
    STAGE3_LABELS,
    AlignOp,
    ArbitrationResult,
    AsrWord,
    CoverageCandidate,
    LaneAResult,
    PassageToken,
    PhonemeToken,
    PhoneticCheck,
    PreparedPassage,
    TraceStep,
    Unit,
    WordLabel,
    WordVerdict,
)


def _unit(**kw):
    base = dict(
        surface="cat",
        key="cat",
        passage_index=0,
        passage_span=1,
        source_indices=(0,),
        start_ms=0,
        end_ms=10,
        align_score=1.0,
    )
    return Unit(**{**base, **kw})


def _verdict(**kw):
    base = dict(
        unit_index=0,
        expected_phonemes=(("k", "æ", "t"),),
        best_variant_index=0,
        heard_phonemes=("k", "æ", "p"),
        distance_expected=0.33,
        distance_hypothesis=0.5,
        deviations=(("t", "p"),),
        stage3_label=WordLabel.SUBSTITUTION,
        label=WordLabel.SUBSTITUTION,
        phonetic_check=PhoneticCheck.DONE,
    )
    return WordVerdict(**{**base, **kw})


def _all_models():
    passage, lane_a = load_fixture("synthetic_cat")
    verdict = _verdict()
    token = PhonemeToken(symbol="k", start_ms=0, end_ms=20, confidence=0.9)
    return [
        passage,
        passage.tokens[0],
        lane_a,
        lane_a.units[0],
        lane_a.trace[0],
        lane_a.asr_words[0],
        token,
        CoverageCandidate(start_ms=0, end_ms=20, phonemes=(token,), after_passage_index=None),
        verdict,
        ArbitrationResult(
            verdicts=(verdict,),
            coverage_candidates=(),
            ms_per_phoneme=(None,),
            phonetic_check=PhoneticCheck.DONE,
        ),
    ]


@pytest.mark.parametrize("model", _all_models(), ids=lambda m: type(m).__name__)
def test_round_trip(model):
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_frozen():
    unit = _unit()
    with pytest.raises(ValidationError):
        unit.key = "x"
    passage, _ = load_fixture("synthetic_cat")
    with pytest.raises(TypeError):
        passage.pronunciations["new"] = (("a",),)


def test_extra_forbidden():
    with pytest.raises(ValidationError):
        PassageToken(index=0, raw="a", key="a", extra=1)


def test_label_sets():
    assert ARBITRATION_LABELS == STAGE3_LABELS
    assert WordLabel.REPETITION not in STAGE3_LABELS


@pytest.mark.parametrize(
    "kw",
    [
        dict(start_ms=-1, end_ms=5),
        dict(start_ms=10, end_ms=5),
        dict(start_ms=5, end_ms=None),
        dict(start_ms=None, end_ms=5),
    ],
)
def test_bad_times(kw):
    with pytest.raises(ValidationError):
        _unit(**kw)
    with pytest.raises(ValidationError):
        AsrWord(index=0, text="a", score=0.5, **kw)


def test_phoneme_token_times_required():
    with pytest.raises(ValidationError):
        PhonemeToken(symbol="k", start_ms=10, end_ms=5, confidence=0.5)
    with pytest.raises(ValidationError):
        PhonemeToken(symbol="k", start_ms=None, end_ms=5, confidence=0.5)


def test_coverage_candidate_times():
    with pytest.raises(ValidationError):
        CoverageCandidate(start_ms=10, end_ms=5, phonemes=(), after_passage_index=None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1])
def test_bad_scores(bad):
    with pytest.raises(ValidationError):
        AsrWord(index=0, text="a", start_ms=0, end_ms=1, score=bad)
    with pytest.raises(ValidationError):
        PhonemeToken(symbol="k", start_ms=0, end_ms=1, confidence=bad)


def test_non_finite_align_score():
    with pytest.raises(ValidationError):
        _unit(align_score=float("nan"))


@pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf")])
def test_bad_distances(bad):
    with pytest.raises(ValidationError):
        _verdict(distance_expected=bad)
    with pytest.raises(ValidationError):
        _verdict(distance_hypothesis=bad)


@pytest.mark.parametrize("symbol", ["", "a b", " ", "a\t"])
def test_bad_symbols(symbol):
    with pytest.raises(ValidationError):
        PhonemeToken(symbol=symbol, start_ms=0, end_ms=1, confidence=0.5)
    with pytest.raises(ValidationError):
        _verdict(heard_phonemes=(symbol,))
    with pytest.raises(ValidationError):
        _verdict(expected_phonemes=((symbol,),))


def test_deviation_rules():
    _verdict(deviations=(("", "x"), ("y", "")))  # insertion and deletion are fine
    with pytest.raises(ValidationError):
        _verdict(deviations=(("", ""),))
    with pytest.raises(ValidationError):
        _verdict(deviations=(("a b", "x"),))


def test_best_variant_index_range():
    with pytest.raises(ValidationError):
        _verdict(best_variant_index=1)
    with pytest.raises(ValidationError):
        _verdict(expected_phonemes=(), best_variant_index=0)
    _verdict(expected_phonemes=(), best_variant_index=None)


def test_degraded_path_keeps_stage3_label():
    with pytest.raises(ValidationError):
        _verdict(phonetic_check=PhoneticCheck.SKIPPED, label=WordLabel.CORRECT)
    with pytest.raises(ValidationError):
        _verdict(phonetic_check=PhoneticCheck.UNVERIFIABLE, label=WordLabel.CORRECT)
    _verdict(phonetic_check=PhoneticCheck.DONE, label=WordLabel.CORRECT)


def test_verdict_label_sets():
    with pytest.raises(ValidationError):
        _verdict(label=WordLabel.REPETITION, stage3_label=WordLabel.REPETITION)
    with pytest.raises(ValidationError):
        _verdict(stage3_label=WordLabel.REVERSAL)


def test_unit_span_rules():
    with pytest.raises(ValidationError):
        _unit(passage_index=None, passage_span=1)
    with pytest.raises(ValidationError):
        _unit(passage_index=0, passage_span=0)


@pytest.mark.parametrize(
    "kw",
    [
        dict(op=AlignOp.MATCH, passage_index=0),
        dict(op=AlignOp.SUB, asr_index=0),
        dict(op=AlignOp.DELETE, passage_index=0, asr_index=0),
        dict(op=AlignOp.DELETE),
        dict(op=AlignOp.INSERT, passage_index=0),
        dict(op=AlignOp.INSERT),
    ],
)
def test_bad_trace_steps(kw):
    with pytest.raises(ValidationError):
        TraceStep(**kw)


def test_lane_a_result_rules():
    _, lane_a = load_fixture("synthetic_cat")
    data = lane_a.model_dump()
    with pytest.raises(ValidationError):  # length mismatch
        LaneAResult.model_validate({**data, "labels": data["labels"][:-1]})
    with pytest.raises(ValidationError):  # post-pass label
        LaneAResult.model_validate({**data, "labels": ["repetition", *data["labels"][1:]]})
    with pytest.raises(ValidationError):  # out of passage order
        LaneAResult.model_validate(
            {**data, "units": data["units"][::-1], "labels": data["labels"][::-1]}
        )


def test_prepared_passage_rules():
    passage, _ = load_fixture("synthetic_cat")
    data = passage.model_dump()
    bad_tokens = [{**t, "index": 5} if t["index"] == 0 else t for t in data["tokens"]]
    with pytest.raises(ValidationError):  # wrong token index
        PreparedPassage.model_validate({**data, "tokens": bad_tokens})
    with pytest.raises(ValidationError):  # key without pronunciation
        PreparedPassage.model_validate({**data, "pronunciations": {"the": (("ð",),)}})
    with pytest.raises(ValidationError):  # only empty variants
        PreparedPassage.model_validate(
            {**data, "pronunciations": {**data["pronunciations"], "cat": ((),)}}
        )


def test_arbitration_result_rules():
    v0, v1 = _verdict(), _verdict(unit_index=1)
    ok = dict(coverage_candidates=(), phonetic_check=PhoneticCheck.DONE)
    ArbitrationResult(verdicts=(v0, v1), ms_per_phoneme=(10.0, None), **ok)
    with pytest.raises(ValidationError):  # length mismatch
        ArbitrationResult(verdicts=(v0, v1), ms_per_phoneme=(10.0,), **ok)
    with pytest.raises(ValidationError):  # wrong unit_index
        ArbitrationResult(verdicts=(v1, v0), ms_per_phoneme=(1.0, 1.0), **ok)
    with pytest.raises(ValidationError):  # negative
        ArbitrationResult(verdicts=(v0,), ms_per_phoneme=(-1.0,), **ok)


def test_synthetic_fixture():
    passage, lane_a = load_fixture("synthetic_cat")
    assert len(passage.tokens) == 7
    assert set(lane_a.labels) == STAGE3_LABELS
    assert [u.verifiable for u in lane_a.units] == [
        True,
        True,
        False,
        False,
        True,
        True,
        True,
        True,
    ]
    by_label = dict(zip(lane_a.labels, lane_a.units, strict=True))
    assert by_label[WordLabel.OMISSION].surface == ""
    assert by_label[WordLabel.OMISSION].source_indices == ()
    assert by_label[WordLabel.INSERTION].passage_index is None
    assert by_label[WordLabel.INSERTION].passage_span == 0
    assert lane_a.labels[2] == WordLabel.CORRECT and not lane_a.units[2].verifiable
    assert len(lane_a.trace) == len(lane_a.units)
    for step, unit in zip(lane_a.trace, lane_a.units, strict=True):
        assert step.passage_index == unit.passage_index
        assert step.asr_index == (unit.source_indices[0] if unit.source_indices else None)
    assert [w.index for w in lane_a.asr_words] == list(range(len(lane_a.asr_words)))
