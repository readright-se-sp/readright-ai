# Phase B6a: Join window fix on real ASR timings (no labels needed)

Status: approved (user, 2026-11-02), with the recommended answer for every open question (Q1–Q4). Ready for implementation, starting at Step 0.

Branch: `feature/lane-b-integration` (continue on it; I1's commits are already there).

## Why

I1's Step 6 (see `phase-I1.md`, "Results") found that Lane B overruled 88 of 735 Lane A `match` rows to `mispronunciation`, almost all of them falsely. The cause is the CrisperWhisper word windows from whisperx, which are much tighter than the `transformers` timings `JOIN_PAD_MS=50` was set on (about 40 ms for "a"). So short words get only their last phoneme (`it` → `/t/`). A diagnostic on one recording showed pad 150 cutting the false flags from 16 to 4.

B6a fixes the window rule and picks its settings using a metric that doesn't need teacher labels. B6b (thresholds, accent weights, the `<=` at `T_FLAG`, near-equal vowels) comes after it and does need labels.

## Decisions already made (user, 2026-11-02)

- I1 is pushed (no PR yet). `PHONETIC_ENABLED` stays `True` on this branch so it can be measured, and no PR is opened until B6a is done.
- **Ending rows:** if Lane A tagged a row `mispronunciation` with detail `ending`, Lane B may confirm it or clear it to `match`, but never change it to `substitution`. This matches Phil-IRI's treatment of endings.

## Step 0: housekeeping

- Push `feature/lane-b-integration` to origin (`git push -u origin feature/lane-b-integration`). No PR.
- Replace the borrowed ffmpeg (Free Download Manager's v4.3.1) with a real install (`winget install Gyan.FFmpeg`, or a static build on PATH). Add "ffmpeg on PATH" to `audio/README.md`'s setup section.

## Step 1: cache the slow parts once (`devtools/cache_runs.py`)

ASR is 14 to 32 s per recording; the join, compare and arbitration are under 0.05 s. A sweep must not re-run the models.
- For each WAV in `audio/data/`, run Lane A's transcriber and Lane B's recogniser once. Save to the git-ignored `audio/devtools/out/cache/<recording>.json`:
  - the `SpokenWord` list,
  - the Lane A rows (`WordLane.rows` output, before review),
  - the `PhonemeToken` list,
  - the passage id,
  - model ids and a settings hash.
- `--force` re-runs. The cache is invalid when the model id changes.
- Add a replay entry point that runs reviewer + post-pass + scorer from the cache with settings overrides. Refactor `PhoneticReviewer` minimally so the recogniser output can be injected: an optional `tokens` argument or a recogniser stub. No change to the hook signature.

## Step 2: the window rule (`lane_b/join.py`)

Replace the fixed pad with **midpoint windows**:
- For consecutive timed units A and B with a gap between `A.end` and `B.start`, phonemes in the gap go to A if their midpoint is before the gap's midpoint, and to B otherwise.
- Each side's reach is capped at `JOIN_MAX_REACH_MS` beyond its own edge.
- Phonemes beyond both caps stay unassigned, and become coverage candidates or `discarded` exactly as now. `COVERAGE_MAX_GAP_MS` and `COVERAGE_MIN_MS` are unchanged.
- Untimed units still get nothing.
- Overlapping windows keep the current tie order (unpadded containment, nearest edge, centre, index).
- The first unit reaches back at most `JOIN_MAX_REACH_MS` before its start, and the last reaches forward the same way.

Setting: `JOIN_MODE` (`midpoint` | `pad`, default `midpoint`) and `JOIN_MAX_REACH_MS` (start 200). Keep `JOIN_PAD_MS` for `pad` mode so the old rule can still be compared. Q1 is whether to drop `pad` mode after B6a.

Tests:
- A gap split at its midpoint.
- The cap leaves far phonemes unassigned.
- First and last units.
- A zero-length gap (touching units).
- Overlapping units.
- Untimed units.
- The one-owner invariant (every phoneme is in exactly one of: a unit, a candidate, `discarded`).
- `pad` mode gives the B3 results unchanged.

## Step 3: coverage guard (`arbitration/arbiter.py`)

A Stage 3 `match` row is only overruled when Lane B heard enough of the word:
- **The guard:** `len(heard) >= ceil(COVERAGE_GUARD_RATIO × n_expected_shortest)`.
- **Below the guard:** the label stays `match`, `phonetic_check = unverifiable`, and the reason is `low_coverage` (logged, plus in the `phonetic` evidence if it has a reason field; add one if not, as an optional `reason`, and note it in `audio-service.md` with no version bump if the field is optional and additive; see Q2).
- The guard only applies to overruling `match`. Clearing a SUB or MISPRON to `match` and the omission rules are unchanged.

Setting: `COVERAGE_GUARD_RATIO` (start 0.5).

Also in this step:
- **Ending rule (decided above).** In `reviewer.py`'s write-back, a row with Lane A tag `mispronunciation` and detail `ending` can only end as `mispronunciation` or `match`. Arbitration's `substitution` becomes `mispronunciation` with detail `ending` kept. Test it with `buys`/`buy`.
- **Adaptive overrule (`ce39c85`).** It was a patch for one bleed phoneme under the old pad. Keep it, but sweep with it on and off (Q3).

Tests: guard boundary (exactly at the ratio, one below); the guard doesn't block SUB→match; the ending rule; an omission is unaffected.

## Step 4: label-free sweep (`devtools/sweep_join.py`)

Runs from the Step 1 cache only, with no model loads.

**Proxy metric.** On these recordings, CrisperWhisper verbatim plus Lane A's `match` is nearly always a correct read. So:
- **False-overrule rate (FOR)** = Lane A `match` rows changed to anything else ÷ all Lane A `match` rows Lane B judged (`check == done`). Lower is better. **Target ≤ 2%** (I1: 88/≈600 ≈ 15%).
- **Coverage rate** = `check == done` ÷ all judged-eligible rows. If the guard wins by skipping everything, Lane B is useless. **Keep coverage ≥ 85%.**
- Also reported, but not optimised:
  - the number of SUB/MISPRON → match clears (the useful direction),
  - counted errors per recording vs Lane A alone,
  - the hand-checked rows from I1 (see below) with their new labels.

**Grid.**

| Setting | Values |
|---|---|
| `JOIN_MODE` | `pad`, `midpoint` |
| `JOIN_PAD_MS` (pad mode only) | 50, 100, 150, 200 |
| `JOIN_MAX_REACH_MS` (midpoint mode only) | 100, 150, 200, 300 |
| `COVERAGE_GUARD_RATIO` | 0, 0.34, 0.5, 0.67 |
| adaptive overrule | on, off |

`T_FLAG`/`T_OVERRULE` stay fixed at 0.25/0.45; that's B6b.

**Selection rule (fixed before looking).**
1. Among configs with coverage ≥ 85%, take the lowest FOR.
2. If several configs are within 0.5 percentage points of it, take the highest coverage.
3. Then prefer `midpoint` over `pad`, the guard off over on, and the adaptive overrule off over on (simpler wins).

**Hold-out.** Use 6 recordings for selection and 3 for confirmation, split by passage so no passage is in both. Choose the split once, record it, and don't change it. Report FOR and coverage on the held-out 3 and don't tune on them.

**Output:** `audio/devtools/out/sweep_join.csv` (git-ignored), with the top 10 configs and the chosen one copied into this file under "Results".

**Hand-checked set.** Store the I1 hand-checked rows as `audio/tests/fixtures/handchecked_i1.yaml`. These are recording ids, passage index, expected final label and note only, no audio. Turn them into a regression test that runs from the cache, skipped if the cache is missing. The rows known to be wrong today (jacky, cups, mongo) are marked `xfail` with reason "B6b".

## Step 5: set the defaults and re-run

- Set the chosen values as `config.py` defaults.
- Re-run `devtools/run_pipeline.py` (real models, all 9) once to confirm the replay matched the live run.
- Record FOR, coverage, counted errors per recording vs Lane A alone, and timing under "Results".

**Done when:**
- Held-out FOR ≤ 2% with coverage ≥ 85%. If no config reaches that, stop and report the best frontier and the top false overrules by pattern, instead of loosening the targets.
- Tests, ruff and pyright (Lane A files, no new errors beyond the 34 pre-existing) pass.
- ROADMAP gets a B6a row and decisions.
- `docs/deployment-architecture.md` is updated if a setting the server cares about changed.

## Open questions (recommendation first)

- **Q1 keep `pad` mode?** Recommendation: remove it after B6a if `midpoint` wins, so there is one rule. Keep it if `pad` wins.
- **Q2 the `reason` field on `phonetic`.** Recommendation: add an optional `reason` (`low_coverage`, `name_not_in_lexicon`, `no_heard`, …) to the `audio-service.md` row's `phonetic` object. It's additive and optional, so bump `CONTRACT_VERSION` 5 → 6 anyway, because the contract rules say any shape change bumps it.
- **Q3 the adaptive overrule.** Recommendation: let the sweep decide. If the guard plus midpoint windows make it irrelevant (FOR within 0.5 points with it off), remove it.
- **Q4 a FOR target of 2% assumes Lane A `match` ≈ truth.** CrisperWhisper can normalise a slight mispronunciation into the right word, so some "false" overrules are real. Recommendation: accept it for B6a, and have B6b's labels measure how often that happens.

## After B6a

- **B6b (needs labels).**
  - **Labels:** pre-fill a CSV from the pipeline's final labels (`recording, passage_index, passage_word, heard, lane_a_tag, final_tag, label`), and the user corrects the `label` column (about 735 rows, a few hours).
  - **Tune:** `T_FLAG`, `T_OVERRULE`, the near-equal vowel accent pairs (cups/cops, mongo/mango) and the `<=` at `T_FLAG` (jacky/jack).
  - **Hold-out:** the same split as B6a.
- **Open the I1 + B6a PR to `dev`** once B6a is done. The Lane A developer reviews it.
- **I2:** the audio worker API (see `phase-I1.md`, "After I1").
