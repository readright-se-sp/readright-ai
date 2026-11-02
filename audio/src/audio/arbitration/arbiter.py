"""Arbitration (spec 5.6 steps 4-5): final label per unit from Stage 3, join and compare."""

import logging
from collections.abc import Sequence

from audio.lane_b.compare import compare, unit_variants
from audio.types import (
    ArbitrationResult,
    JoinResult,
    LaneAResult,
    PhoneSeq,
    PhoneticCheck,
    PreparedPassage,
    WordLabel,
    WordVerdict,
)

logger = logging.getLogger(__name__)

_NEAR = 0.05  # near-threshold log window
_L = WordLabel


def _label(stage3, d_exp, d_hyp, deviations, heard, t_flag, t_overrule):
    if stage3 == _L.CORRECT:
        if d_exp > t_overrule and deviations:
            return _L.MISPRONUNCIATION
        return _L.CORRECT
    if stage3 in (_L.SUBSTITUTION, _L.MISPRONUNCIATION):
        if d_exp <= t_flag:
            return _L.CORRECT
        if d_hyp is None:
            return stage3
        return _L.SUBSTITUTION if d_hyp < d_exp else _L.MISPRONUNCIATION
    if stage3 == _L.OMISSION:
        return _L.CORRECT if heard and d_exp <= t_flag else _L.OMISSION
    return stage3  # INSERTION passes through


def arbitrate(
    lane_a: LaneAResult,
    passage: PreparedPassage,
    join: JoinResult | None,
    hypothesis: Sequence[tuple[PhoneSeq, ...]] | None,
    *,
    t_flag: float,
    t_overrule: float,
) -> ArbitrationResult:
    n = len(lane_a.units)
    if join is not None and (len(join.unit_phonemes) != n or len(join.ms_per_phoneme) != n):
        raise ValueError("join must have one entry per unit")
    if hypothesis is not None and len(hypothesis) != n:
        raise ValueError("hypothesis must have one entry per unit")
    if join is None:
        logger.warning("phonetic_check=skipped")

    verdicts = []
    for i, (unit, stage3) in enumerate(zip(lane_a.units, lane_a.labels, strict=True)):
        expected = unit_variants(unit, passage)
        heard = tuple(t.symbol for t in join.unit_phonemes[i]) if join else ()
        if join is None:
            check = PhoneticCheck.SKIPPED
        elif not unit.verifiable or (not heard and stage3 != _L.OMISSION):
            check = PhoneticCheck.UNVERIFIABLE
        else:
            check = PhoneticCheck.DONE

        done = check == PhoneticCheck.DONE
        exp = compare(heard, expected) if done and expected else None
        hyp = None
        if done and stage3 in (_L.SUBSTITUTION, _L.MISPRONUNCIATION) and hypothesis:
            hyp = compare(heard, hypothesis[i]) if hypothesis[i] else None
        d_hyp = hyp.distance if hyp else None

        label = stage3
        if exp is not None:
            label = _label(stage3, exp.distance, d_hyp, exp.deviations, heard, t_flag, t_overrule)
            threshold = t_overrule if stage3 == _L.CORRECT else t_flag
            if abs(exp.distance - threshold) <= _NEAR:
                logger.info(
                    "near_threshold unit=%d stage3=%s label=%s d_exp=%.3f threshold=%s",
                    i,
                    stage3.value,
                    label.value,
                    exp.distance,
                    threshold,
                )

        verdicts.append(
            WordVerdict(
                unit_index=i,
                expected_phonemes=expected,
                best_variant_index=exp.best_variant_index if exp else None,
                heard_phonemes=heard,
                distance_expected=exp.distance if exp else None,
                distance_hypothesis=d_hyp,
                deviations=exp.deviations if exp else (),
                stage3_label=stage3,
                label=label,
                phonetic_check=check,
            )
        )

    return ArbitrationResult(
        verdicts=tuple(verdicts),
        coverage_candidates=join.coverage_candidates if join else (),
        ms_per_phoneme=join.ms_per_phoneme if join else (None,) * n,
        phonetic_check=PhoneticCheck.SKIPPED if join is None else PhoneticCheck.DONE,
    )
