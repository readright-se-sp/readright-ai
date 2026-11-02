# Phase B3 plan — Join by time and coverage candidates

Status: approved (open questions: recommendations accepted). Run it after B1.1 is committed (no file overlap, but one phase at a time on `feature/lane-b`). Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

Spec §5.6 step 2: give every Lane B phoneme to exactly one hypothesis unit or one coverage candidate, by time. Also compute the per-unit duration figure the post-pass needs (§5.7, `DURATION_RATIO`).

B3 is a pure function plus tests. It needs no recordings, no model and no espeak. Measuring the settings on real recordings is B6's job.

## Files

| File | Change |
|---|---|
| `src/audio/types.py` | add `JoinResult` under "Lane B intermediate" |
| `src/audio/settings.py` | add three settings |
| `src/audio/lane_b/__init__.py` | new, empty |
| `src/audio/lane_b/join.py` | new: `join(...)` |
| `tests/test_join.py` | new |
| `ROADMAP.md`, `../README.md` | status, decisions |

No new dependencies.

## Types

```python
class JoinResult(_Model):
    unit_phonemes: tuple[tuple[PhonemeToken, ...], ...]  # one entry per unit, in unit order
    coverage_candidates: tuple[CoverageCandidate, ...]  # in time order
    discarded: tuple[PhonemeToken, ...]  # runs shorter than COVERAGE_MIN_MS
    ms_per_phoneme: tuple[NonNegFloat | None, ...]  # one entry per unit
```

Validator: `len(unit_phonemes) == len(ms_per_phoneme)`. Use the same `Annotated[float, Field(ge=0, allow_inf_nan=False)]` as `ArbitrationResult.ms_per_phoneme` (give it a name and use it in both places).

`discarded` exists so the spec invariant "every Lane B phoneme is assigned to exactly one unit or one coverage candidate" can be checked in full: units + candidates + discarded = input.

## Settings

```python
# Join by time (B3). Starting values; B6 measures them.
join_pad_ms: int = 80  # AUDIO_JOIN_PAD_MS, try 50–100
coverage_min_ms: int = 120  # AUDIO_COVERAGE_MIN_MS
coverage_max_gap_ms: int = 300  # AUDIO_COVERAGE_MAX_GAP_MS, silence that ends a run
```

## `join.py`

```python
def join(
    units: Sequence[Unit],
    phonemes: Sequence[PhonemeToken],
    passage: PreparedPassage,
    *,
    pad_ms: int,
    coverage_min_ms: int,
    coverage_max_gap_ms: int,
) -> JoinResult:
```

Pure: no settings import, no logging of phoneme content. B7 passes the settings in.

**Assignment.** Sort the phonemes by `(start_ms, end_ms)`. For each one, `mid = (start_ms + end_ms) / 2`. Candidates are the timed units (`verifiable`) whose padded window `[start_ms − pad_ms, end_ms + pad_ms]` contains `mid`, edges included. If there is none, the phoneme is unassigned. Otherwise pick in this order (open question 1):
1. Units whose unpadded `[start_ms, end_ms]` contains `mid` beat units that contain it only in the padding.
2. Then the smallest distance from `mid` to the unpadded interval (0 when inside).
3. Then the nearer centre.
4. Then the lower unit index.

A plain scan over all units per phoneme is fine (`# ponytail: O(phonemes × units), about 10^5 steps for a 60 s recording; bisect on sorted windows if profiling says so`).

Units with `verifiable=False` get `()`. Omission units (`source_indices == ()`) that have timing (the gap where the word should have been) are joined like any other unit. That is what lets B5 check "speech in the gap".

**Coverage runs.** Walk the sorted phonemes. Consecutive unassigned phonemes form a run. A run ends at an assigned phoneme, or when the silence between two unassigned phonemes (`next.start_ms − prev.end_ms`) is more than `coverage_max_gap_ms` (open question 3). For each run:
- duration = `last.end_ms − first.start_ms`
- duration ≥ `coverage_min_ms` → `CoverageCandidate(start_ms, end_ms, phonemes, after_passage_index)`
- otherwise all its phonemes go to `discarded`

`after_passage_index` is the largest `passage_index + passage_span − 1` among units that have a `passage_index` and timing and whose `end_ms <= run.start_ms`. It is `None` when there is no such unit (speech before the first word). Insertion units (`passage_index is None`) and untimed units are ignored here.

**`ms_per_phoneme`** (open question 2). For a unit that has timing, a `passage_index` and at least one ASR word (`source_indices != ()`):

`(end_ms − start_ms) / expected_count`, where `expected_count` is the sum over the unit's passage tokens (`passage_index` to `passage_index + passage_span − 1`) of the shortest variant length in `passage.pronunciations[token.key]`.

Every other unit gets `None` (insertions, omissions, untimed units). If `expected_count` is 0, also `None` (it cannot happen with a valid `PreparedPassage`, but do not divide by zero).

## Tests (`tests/test_join.py`)

Build the units, phoneme tokens and a small `PreparedPassage` inline. No espeak and no fixtures. Pad 80, min 120, max gap 300 unless the test says otherwise.

1. A phoneme inside a unit goes to it. A phoneme only inside the padding goes to that unit. A phoneme just outside the padding is unassigned.
2. Window edges are inclusive (`mid == end_ms + pad_ms`).
3. Two adjacent units, phoneme in the gap within both paddings: the nearer edge wins, even when the other unit's centre is nearer (a long unit next to a short one).
4. Inside one unit's unpadded interval and inside another's padding: the unpadded one wins.
5. Equal distances: the lower unit index wins.
6. An untimed unit gets `()` and `None`, even when phonemes fall where it "would" be.
7. A timed omission unit (`source_indices=()`) receives the phonemes in its gap, and its `ms_per_phoneme` is `None`.
8. A run of exactly `coverage_min_ms` becomes a candidate. A run 1 ms shorter goes to `discarded`.
9. A run split by silence longer than `coverage_max_gap_ms` becomes two runs, each judged on its own.
10. `after_passage_index`: `None` before the first word; it skips an insertion unit; with a `passage_span=2` unit it is the last token that unit covers.
11. `ms_per_phoneme` for a matched unit uses the shortest variant, summed over `passage_span`. Insertions get `None`.
12. Invariant on a mixed case: every input phoneme appears exactly once across `unit_phonemes`, `coverage_candidates[*].phonemes` and `discarded`.
13. Empty phoneme input: every unit gets `()`, there are no candidates and nothing is discarded. `ms_per_phoneme` is still computed.
14. Unsorted input gives the same result as sorted input.
15. `JoinResult` rejects mismatched lengths.

## Done when

- `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` pass. Nothing new is skipped.
- ROADMAP: the B3 row says Done with a link to this plan. The B3 section records the decisions below, and its duration line is updated to the expected-count formula. Root README "Current state" says Lane B has join by time.
- One Conventional Commit on `feature/lane-b`, for example `feat(audio): join phonemes to units by time with coverage candidates (B3)`.
  - No Claude or co-author trailers.
  - Nothing from `audio/data/`, `audio/devtools/out/` or `.env` is staged.
  - No push.

## Open questions (recommended answers; approve or change)

1. **Overlapping windows.** The ROADMAP says "nearer centre". That sends a phoneme just after a long word to a short next word. **Recommend:** unpadded containment first, then the nearest edge, then the nearer centre, then the lower index.
2. **Duration figure.** The ROADMAP says `duration / max(1, heard count)`. A word hiding several attempts ("mon… monk… money") has more heard phonemes as well as a longer duration, so that ratio stays normal and misses the signal. The spec's `DURATION_RATIO` is duration against expected duration. **Recommend:** divide by the expected phoneme count (shortest variant), and use `None` for insertions, omissions and untimed units. The post-pass (Lane A) applies the threshold.
3. **Silence inside a run.** Without a break, two stray phonemes on either side of a 3 s pause make one long "speech" run. **Recommend:** the new setting `AUDIO_COVERAGE_MAX_GAP_MS` (300). B6 measures it with the other two.
4. **Short runs.** **Recommend:** keep them in `JoinResult.discarded` (not passed on) so the invariant test is complete.
5. **Measuring on recordings.** **Recommend:** no devtool in B3. B6 calibrates `JOIN_PAD_MS`, `COVERAGE_MIN_MS` and `COVERAGE_MAX_GAP_MS` with the Lane A units (or Whisper stand-ins) and the teacher labels.

## Decisions (approved with this plan)

1. `join` is a pure function in `lane_b/join.py`. It returns `JoinResult` (added to `types.py`) with `discarded` for short runs.
2. Tie rule and duration formula as in open questions 1 and 2, as approved.
3. Coverage runs also break on silence longer than `AUDIO_COVERAGE_MAX_GAP_MS`.
4. Timed omission units take part in the join; untimed units get no phonemes.
