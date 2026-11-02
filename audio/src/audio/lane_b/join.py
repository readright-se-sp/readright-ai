"""Join by time (spec 5.6 step 2): give each phoneme to one unit or one coverage candidate.

Each timed unit has a window. ``pad`` mode: the unit's span plus a fixed pad each side (B3).
``midpoint`` mode (B6a): the span, extended into each gap to the neighbouring unit by at most half
the gap (so the gap is split at its midpoint) and at most ``max_reach_ms``. The first and last unit
reach ``max_reach_ms`` outward. Phonemes outside every window are coverage candidates or discarded.
"""

from collections.abc import Sequence
from typing import Literal

from audio.types import CoverageCandidate, JoinResult, PhonemeToken, PreparedPassage, Unit

Window = tuple[float, float] | None  # (low, high) in ms, None for an untimed unit


def _pad_windows(units: Sequence[Unit], pad_ms: int) -> list[Window]:
    return [
        (u.start_ms - pad_ms, u.end_ms + pad_ms)
        if u.start_ms is not None and u.end_ms is not None
        else None
        for u in units
    ]


def _midpoint_windows(units: Sequence[Unit], max_reach_ms: int) -> list[Window]:
    """Span extended into each gap by min(half the gap, max_reach_ms); ends by max_reach_ms."""
    timed = [
        (i, u.start_ms, u.end_ms)
        for i, u in enumerate(units)
        if u.start_ms is not None and u.end_ms is not None
    ]
    out: list[Window] = [None] * len(units)
    for i, start, end in timed:
        # nearest other unit boundary after this unit's end / before its start
        next_start = min((s for j, s, _ in timed if j != i and s >= end), default=None)
        prev_end = max((e for j, _, e in timed if j != i and e <= start), default=None)
        after = max_reach_ms if next_start is None else min(max_reach_ms, (next_start - end) / 2)
        before = max_reach_ms if prev_end is None else min(max_reach_ms, (start - prev_end) / 2)
        out[i] = (start - before, end + after)
    return out


def _pick_unit(units: Sequence[Unit], mid: float, windows: Sequence[Window]) -> int | None:
    """Best unit for a midpoint: unpadded containment, then nearest edge, centre, index."""
    best_key = None
    best = None
    # ponytail: O(phonemes x units), about 10^5 steps for a 60 s recording; bisect on sorted
    # windows if profiling says so
    for i, u in enumerate(units):
        window = windows[i]
        if window is None or u.start_ms is None or u.end_ms is None:
            continue
        start, end = u.start_ms, u.end_ms
        if not (window[0] <= mid <= window[1]):
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
    mode: Literal["pad", "midpoint"] = "pad",
    max_reach_ms: int = 200,
) -> JoinResult:
    windows = (
        _pad_windows(units, pad_ms) if mode == "pad" else _midpoint_windows(units, max_reach_ms)
    )
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
        i = _pick_unit(units, (p.start_ms + p.end_ms) / 2, windows)
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
