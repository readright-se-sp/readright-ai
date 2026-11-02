"""Arbitration (spec 5.6 steps 4-5): final label per unit from Stage 3, join and compare."""

import logging
import math
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

# Boundary-bleed correction: for short words (≤ _SHORT_WORD_PHONES expected phonemes),
# one spurious bleed phoneme from an adjacent word raises d_exp by 1/len(expected).
# For a 2-phoneme word that is 0.50 which exceeds the default T_OVERRULE of 0.45.
# We add one indel allowance (1/len) to the overrule threshold only when the extra
# cost comes from insertions (heard > expected), not from substitutions, so that a
# genuine mispronunciation with the same number of phonemes is still caught.
_SHORT_WORD_PHONES = 3  # words with ≤ this many expected phonemes get leniency
_INDEL_COST = 1.0  # must match compare._align indel weight


def _adaptive_overrule(
    t_overrule: float,
    n_expected: int,
    n_heard: int,
) -> float:
    """Return length-adjusted overrule threshold to absorb one boundary-bleed phoneme.

    Leniency is only granted when the student's heard phoneme count exceeds the expected
    count (insertion bleed), not when there are substitutions with the same or fewer
    heard phonemes.  This keeps genuine mispronunciations detectable on short words.
    """
    if n_expected <= _SHORT_WORD_PHONES and n_heard > n_expected:
        return t_overrule + _INDEL_COST / max(n_expected, 1)
    return t_overrule


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
    coverage_guard_ratio: float = 0.0,
    adaptive_overrule: bool = True,
    short_word_max_phones: int = 0,
) -> ArbitrationResult:
    """Final label per unit. A CORRECT word is only overruled when Lane B heard at least
    ceil(coverage_guard_ratio x shortest expected pronunciation) phonemes (0 = no guard); below
    that the label stays and the check is UNVERIFIABLE with reason "low_coverage". A CORRECT word
    whose shortest expected pronunciation has at most short_word_max_phones phonemes (0 = off) is
    never overruled: same outcome with reason "short_word". Clearing and omissions are unaffected.
    """
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
        reason = None
        if join is None:
            check = PhoneticCheck.SKIPPED
        elif not unit.verifiable:
            check, reason = PhoneticCheck.UNVERIFIABLE, "untimed"
        elif not heard and stage3 != _L.OMISSION:
            check, reason = PhoneticCheck.UNVERIFIABLE, "no_heard"
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
            n_exp = len(expected[0]) if expected else 0
            eff_overrule = (
                _adaptive_overrule(t_overrule, n_exp, len(heard))
                if stage3 == _L.CORRECT and adaptive_overrule
                else t_overrule
            )
            label = _label(stage3, exp.distance, d_hyp, exp.deviations, heard, t_flag, eff_overrule)
            if stage3 == _L.CORRECT and label != stage3:
                shortest = min(len(v) for v in expected)
                if shortest <= short_word_max_phones:
                    label, check, reason = stage3, PhoneticCheck.UNVERIFIABLE, "short_word"
                elif coverage_guard_ratio > 0 and len(heard) < math.ceil(
                    coverage_guard_ratio * shortest
                ):
                    label, check, reason = stage3, PhoneticCheck.UNVERIFIABLE, "low_coverage"
            threshold = eff_overrule if stage3 == _L.CORRECT else t_flag
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
                reason=reason,
            )
        )

    return ArbitrationResult(
        verdicts=tuple(verdicts),
        coverage_candidates=join.coverage_candidates if join else (),
        ms_per_phoneme=join.ms_per_phoneme if join else (None,) * n,
        phonetic_check=PhoneticCheck.SKIPPED if join is None else PhoneticCheck.DONE,
    )
