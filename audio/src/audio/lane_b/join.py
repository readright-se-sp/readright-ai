"""Join by time (spec 5.6 step 2): give each phoneme to one unit or one coverage candidate."""

from collections.abc import Sequence

from audio.types import CoverageCandidate, JoinResult, PhonemeToken, PreparedPassage, Unit


def _pick_unit(units: Sequence[Unit], mid: float, pad_ms: int) -> int | None:
    """Best unit for a midpoint: unpadded containment, then nearest edge, centre, index."""
    best_key = None
    best = None
    # ponytail: O(phonemes x units), about 10^5 steps for a 60 s recording; bisect on sorted
    # windows if profiling says so
    for i, u in enumerate(units):
        if not u.verifiable:
            continue
        start, end = u.start_ms, u.end_ms
        if not (start - pad_ms <= mid <= end + pad_ms):
            continue
        inside = start <= mid <= end
        dist = 0 if inside else min(abs(mid - start), abs(mid - end))
        key = (not inside, dist, abs(mid - (start + end) / 2), i)
        if best_key is None or key < best_key:
            best_key, best = key, i
    return best


def _after_index(units: Sequence[Unit], run_start: int) -> int | None:
    last = None
    for u in units:
        if u.passage_index is None or not u.verifiable or u.end_ms > run_start:
            continue
        idx = u.passage_index + u.passage_span - 1
        if last is None or idx > last:
            last = idx
    return last


def _ms_per_phoneme(unit: Unit, passage: PreparedPassage) -> float | None:
    if not unit.verifiable or unit.passage_index is None or not unit.source_indices:
        return None
    expected = 0
    for token in passage.tokens[unit.passage_index : unit.passage_index + unit.passage_span]:
        expected += min(len(v) for v in passage.pronunciations[token.key])
    if expected == 0:
        return None
    return (unit.end_ms - unit.start_ms) / expected


def join(
    units: Sequence[Unit],
    phonemes: Sequence[PhonemeToken],
    passage: PreparedPassage,
    *,
    pad_ms: int,
    coverage_min_ms: int,
    coverage_max_gap_ms: int,
) -> JoinResult:
    per_unit: list[list[PhonemeToken]] = [[] for _ in units]
    candidates: list[CoverageCandidate] = []
    discarded: list[PhonemeToken] = []
    run: list[PhonemeToken] = []

    def close_run() -> None:
        if not run:
            return
        start, end = run[0].start_ms, run[-1].end_ms
        if end - start >= coverage_min_ms:
            candidates.append(
                CoverageCandidate(
                    start_ms=start,
                    end_ms=end,
                    phonemes=tuple(run),
                    after_passage_index=_after_index(units, start),
                )
            )
        else:
            discarded.extend(run)
        run.clear()

    for p in sorted(phonemes, key=lambda p: (p.start_ms, p.end_ms)):
        i = _pick_unit(units, (p.start_ms + p.end_ms) / 2, pad_ms)
        if i is not None:
            close_run()
            per_unit[i].append(p)
            continue
        if run and p.start_ms - run[-1].end_ms > coverage_max_gap_ms:
            close_run()
        run.append(p)
    close_run()

    return JoinResult(
        unit_phonemes=tuple(tuple(ps) for ps in per_unit),
        coverage_candidates=tuple(candidates),
        discarded=tuple(discarded),
        ms_per_phoneme=tuple(_ms_per_phoneme(u, passage) for u in units),
    )
