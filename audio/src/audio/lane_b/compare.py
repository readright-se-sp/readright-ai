"""Compare (spec 5.6 step 3, §6): weighted phoneme edit distance with an accent table."""

import itertools
import logging
from collections.abc import Sequence

from audio.alignment.text import normalize
from audio.passage import PassagePreparationError, PassagePreparer
from audio.phonetics.accent_table import load_accent_table
from audio.phonetics.features import feature_distance
from audio.phonetics.ipa import INVENTORY, VOWELS
from audio.types import Comparison, Deviation, PhoneSeq, PreparedPassage, Unit

logger = logging.getLogger(__name__)

_EPS = 1e-9
_MAX_VARIANTS = 16


def _is_consonant(sym: str) -> bool:
    return sym in INVENTORY and sym not in VOWELS


def _align(heard: Sequence[str], expected: Sequence[str]) -> tuple[float, list[Deviation]]:
    """Weighted Levenshtein; backtrace ties prefer diagonal, then deletion, then insertion."""
    table = load_accent_table()
    costs, pairs = table.costs, table.pairs
    n, m = len(heard), len(expected)

    def sub(e: str, h: str) -> float:
        if e == h:
            return 0.0
        if (e, h) in pairs:
            return costs["accent"]
        floor = costs["substitution_floor"]
        return floor + (1 - floor) * feature_distance(e, h)

    def cluster_drop(j: int) -> bool:
        return (
            j == m - 1 and j >= 1 and _is_consonant(expected[j]) and _is_consonant(expected[j - 1])
        )

    def delete(j: int) -> float:
        return costs["final_cluster_drop"] if cluster_drop(j) else costs["indel"]

    indel = costs["indel"]
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i * indel
    for j in range(1, m + 1):
        d[0][j] = d[0][j - 1] + delete(j - 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(
                d[i - 1][j - 1] + sub(expected[j - 1], heard[i - 1]),
                d[i][j - 1] + delete(j - 1),
                d[i - 1][j] + indel,
            )

    deviations: list[Deviation] = []
    i, j = n, m
    while i > 0 or j > 0:
        here = d[i][j]
        if (
            i > 0
            and j > 0
            and abs(d[i - 1][j - 1] + sub(expected[j - 1], heard[i - 1]) - here) < _EPS
        ):
            e, h = expected[j - 1], heard[i - 1]
            if e != h and (e, h) not in pairs:
                deviations.append((e, h))
            i, j = i - 1, j - 1
        elif j > 0 and abs(d[i][j - 1] + delete(j - 1) - here) < _EPS:
            if not cluster_drop(j - 1):
                deviations.append((expected[j - 1], ""))
            j -= 1
        else:
            deviations.append(("", heard[i - 1]))
            i -= 1
    deviations.reverse()
    return d[n][m], deviations


def compare(heard: Sequence[str], variants: Sequence[PhoneSeq]) -> Comparison | None:
    best: Comparison | None = None
    for index, variant in enumerate(variants):
        if not variant:
            continue
        cost, deviations = _align(heard, variant)
        distance = cost / len(variant)
        if best is None or distance < best.distance:
            best = Comparison(
                distance=distance, best_variant_index=index, deviations=tuple(deviations)
            )
    return best


def _concat_product(per_token: Sequence[Sequence[PhoneSeq]]) -> tuple[PhoneSeq, ...]:
    # ponytail: cap 16; raise if multi-token units with many variants show up
    products = itertools.islice(itertools.product(*per_token), _MAX_VARIANTS)
    return tuple(tuple(sym for seq in combo for sym in seq) for combo in products)


def unit_variants(unit: Unit, passage: PreparedPassage) -> tuple[PhoneSeq, ...]:
    if unit.passage_index is None:
        return ()
    tokens = passage.tokens[unit.passage_index : unit.passage_index + unit.passage_span]
    return _concat_product([passage.pronunciations[t.key] for t in tokens])


def hypothesis_variants(
    units: Sequence[Unit], preparer: PassagePreparer
) -> tuple[tuple[PhoneSeq, ...], ...]:
    empty = tuple(() for _ in units)
    text = " ".join(u.surface for u in units if u.surface)
    if not text:
        return empty
    try:
        prepared = preparer.prepare(text)
    except PassagePreparationError as exc:
        # ponytail: all-or-nothing; per-word fallback if this happens on real data
        logger.warning("hypothesis pronunciations unavailable: %s", exc)
        return empty
    out = []
    for unit in units:
        keys = normalize(unit.surface)
        out.append(_concat_product([prepared.pronunciations[k] for k in keys]) if keys else ())
    return tuple(out)
