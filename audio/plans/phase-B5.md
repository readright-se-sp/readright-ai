# Phase B5 plan — Arbitration

Status: approved (open questions: recommendations accepted). Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

Spec §5.6 steps 4–5. For every unit, build a `WordVerdict` (distances recorded whether or not the label changes) and the final label, from the Stage 3 label plus the B3 join and B4 compare. If Lane B failed, return the Stage 3 labels unchanged with `phonetic_check=skipped`.

Pure function plus tests. No model, espeak, recordings or Lane A code; it uses the interim `LaneAResult`.

## Files

| File | Change |
|---|---|
| `src/audio/arbitration/__init__.py` | new, empty |
| `src/audio/arbitration/arbiter.py` | new: `arbitrate(...)` |
| `src/audio/settings.py` | `t_flag`, `t_overrule` and a validator |
| `tests/test_arbiter.py`, `tests/test_settings.py` | new |
| `ROADMAP.md`, `../README.md` | status, B5 rule-table fixes, decisions |

No new dependencies.

## Settings

```python
# Arbitration (B5). Starting values; B6 tunes them (spec §9: measure, do not pick in discussion).
t_flag: float = 0.25  # AUDIO_T_FLAG
t_overrule: float = 0.45  # AUDIO_T_OVERRULE
```

Add a `model_validator(mode="after")` on `AudioSettings` that raises `ValueError` unless `0 <= t_flag < t_overrule`. `AudioSettings()` is built at startup, so a bad pair fails startup.

## `arbiter.py`

```python
def arbitrate(
    lane_a: LaneAResult,
    passage: PreparedPassage,
    join: JoinResult | None,                               # None: Lane B failed or timed out
    hypothesis: Sequence[tuple[PhoneSeq, ...]] | None,     # from hypothesis_variants; None: unavailable
    *,
    t_flag: float,
    t_overrule: float,
) -> ArbitrationResult:
```

Raise `ValueError` if `join` or `hypothesis` (when given) does not have one entry per unit.

**Per unit `i`** (`unit`, `stage3 = lane_a.labels[i]`):
- `expected = unit_variants(unit, passage)`
- `heard = tuple(t.symbol for t in join.unit_phonemes[i])` (or `()` when `join is None`)
- `phonetic_check`:
  - `SKIPPED` when `join is None`
  - else `UNVERIFIABLE` when the unit is not `verifiable`, or when `heard == ()` and `stage3 != OMISSION` (open question 1)
  - else `DONE`
- `exp = compare(heard, expected)` when the check is `DONE` and `expected` is non-empty, else `None`. The verdict gets `distance_expected = exp.distance`, `best_variant_index = exp.best_variant_index` and `deviations = exp.deviations` (`None`, `None` and `()` when `exp` is `None`).
- `hyp`: only for `SUBSTITUTION` and `MISPRONUNCIATION` with the check `DONE`. It is `compare(heard, hypothesis[i])` when `hypothesis` is given and `hypothesis[i]` is non-empty, else `None`. `distance_hypothesis = hyp.distance` or `None`. For matched words it is always `None` (ROADMAP B4).

**Label** (only when the check is `DONE` and `exp` is not `None`; otherwise `label = stage3`):

| Stage 3 | Condition | Final |
|---|---|---|
| CORRECT | `d_exp ≤ t_overrule` | CORRECT |
| CORRECT | `d_exp > t_overrule` and `deviations` non-empty | MISPRONUNCIATION |
| CORRECT | `d_exp > t_overrule` and `deviations` empty | CORRECT |
| SUB / MISPRON | `d_exp ≤ t_flag` | CORRECT |
| SUB / MISPRON | `d_hyp` not `None` and `d_hyp < d_exp` | SUBSTITUTION |
| SUB / MISPRON | `d_hyp` not `None` otherwise ("far from both") | MISPRONUNCIATION |
| SUB / MISPRON | `d_hyp` is `None` | Stage 3 label (open question 2) |
| OMISSION | `heard == ()` | OMISSION |
| OMISSION | `d_exp ≤ t_flag` | CORRECT (VAD or ASR dropped a spoken word) |
| OMISSION | otherwise | OMISSION |
| INSERTION | any | INSERTION (passes through to the post-pass) |

An omission's speech is its own `heard` phonemes. B3 joins timed omission units over their gap, so the gap's phonemes land on the unit, not in a coverage candidate (open question 3).

**Result.**
- `verdicts`: one `WordVerdict` per unit, `unit_index = i`
- `coverage_candidates = join.coverage_candidates`, or `()`
- `ms_per_phoneme = join.ms_per_phoneme`, or `None` per unit
- `phonetic_check = SKIPPED` when `join is None`, else `DONE`

**Near-threshold log** (ROADMAP B5 open question: no abstention in v1; keep the data). When the check is `DONE` and `d_exp` is within ±0.05 of the threshold the rule used (`t_overrule` for CORRECT, `t_flag` otherwise), `logger.info("near_threshold unit=%d stage3=%s label=%s d_exp=%.3f threshold=%s", ...)`. Log no passage or phoneme text.

When `join is None`, log `logger.warning("phonetic_check=skipped")` once (ROADMAP: log-only, since the contract has no field for it).

## Tests (`tests/test_arbiter.py`)

Build the passage, units, `JoinResult` and hypothesis variants inline, with small hand-written pronunciations. No G2P. Use `t_flag=0.25` and `t_overrule=0.45`.

1. Table-driven: one case per row of the label table above, asserting `label`, `phonetic_check` and that the distances are recorded.
2. Boundaries: `d_exp == t_overrule` stays CORRECT; `d_exp == t_flag` clears SUB to CORRECT. Pick heard and expected sequences whose distance hits the threshold exactly, or patch `compare` with `monkeypatch` to return a fixed `Comparison`; the latter is simpler and preferred.
3. CORRECT with `d_exp > t_overrule` but no deviations stays CORRECT.
4. Distances are recorded on an unchanged label (CORRECT stays CORRECT, with `distance_expected` set).
5. Degraded (`join=None`): every label equals Stage 3, every verdict and the result are `SKIPPED`, coverage is `()`, `ms_per_phoneme` is all `None`, and one warning is logged.
6. An unverifiable unit keeps its Stage 3 label with `UNVERIFIABLE`.
7. A timed CORRECT unit with no heard phonemes gives `UNVERIFIABLE`, label unchanged.
8. `hypothesis=None` gives `distance_hypothesis=None` everywhere; a SUB stays SUB.
9. `distance_hypothesis` is `None` for CORRECT units even when hypothesis variants are given.
10. Coverage candidates and `ms_per_phoneme` are passed through from `join`.
11. A length mismatch raises `ValueError`.
12. The near-threshold log fires within ±0.05 and not outside it (`caplog`).
13. Settings: `AudioSettings(t_flag=0.5, t_overrule=0.4)` raises; equal values raise; the defaults load.

## Done when

- `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` pass. Nothing new is skipped.
- ROADMAP:
  - the B5 row says Done with a link to this plan
  - in the B5 section, the OMISSION rows say "the unit's own heard phonemes" (not a coverage candidate)
  - the B5 section adds the "no heard phonemes" and "no hypothesis" rows
  - the decisions below are recorded
- Root README "Current state" mentions arbitration.
- One Conventional Commit on `feature/lane-b` including this plan, for example `feat(audio): add arbitration of Stage 3 labels with phonetic evidence (B5)`.
  - No Claude or co-author trailers.
  - Nothing from `audio/data/`, `audio/devtools/out/` or `.env` is staged.
  - No push.

## Open questions (recommended answers; approve or change)

1. **A timed word with no heard phonemes.** The distance would be about 1.0 (all deletions), which turns a matched word into MISPRONUNCIATION. That is far more likely to be a recogniser gap (quiet speech) than a misread word. **Recommend:** `UNVERIFIABLE`, keep the Stage 3 label. Omissions are the exception, because "no speech" is the evidence there.
2. **SUB/MISPRON without a hypothesis distance** (G2P failed or the surface was empty). **Recommend:** keep the Stage 3 label instead of guessing.
3. **The omission gap.** The ROADMAP says "coverage candidate in the gap"; since B3, gap phonemes go to the omission unit itself. **Recommend:** use the unit's own heard phonemes and fix the ROADMAP text. Known limit: when the gap holds unrelated speech (a hidden attempt), it is absorbed into the omission rather than surfacing as a coverage candidate. Look at it in B6.
4. **Starting thresholds.** Distances are normalised per expected phoneme, so one non-accent substitution in a 3-phoneme word scores about 0.15–0.35, and cat→cap is about 0.17, under `T_FLAG` 0.25. **Recommend:** keep the ROADMAP's starting values (the spec forbids picking them in discussion), and make "short-word substitutions" the first check in B6.
5. **Abstention (spec §11).** **Recommend:** leave it out of v1, with the near-threshold log as above (the ROADMAP's recommendation).

## Decisions (approved with this plan)

1. `arbitrate` is pure and builds the `WordVerdict`s itself (it calls B4's `compare` and `unit_variants`). `join=None` is the degraded path.
2. `phonetic_check` per word: `SKIPPED` (Lane B absent), `UNVERIFIABLE` (untimed, or no heard phonemes on a non-omission), else `DONE`.
3. The rules are as in the label table, including the no-hypothesis and no-deviation rows.
4. Omissions are judged on their own joined phonemes.
5. Thresholds are settings, validated `0 <= T_FLAG < T_OVERRULE` at startup.
