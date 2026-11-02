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

**The split (fixed 2026-11-02, before any sweep; do not change).** The 9 recordings cover 6 passages: `every_saturday` (3 recordings), `marian_came` (2), `a_mouse_and_a`, `mama_was`, `nick_is_a_tri`, `ring_ring` (1 each).
- **Selection (6):** the 3 `every_saturday` recordings, `a_mouse_and_a`, `mama_was`, `ring_ring`.
- **Held-out (3):** the 2 `marian_came` recordings and `nick_is_a_tri`.
- No passage is in both sets. Recording stems (prefix of the file name) are the ids: selection `everysatur*`, `amouseanda*`, `mamawas*`, `ringringra*`; held-out `mariancame*`, `nickisatri*`.

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

## Results (partial: stopped at Step 4, 2026-11-02)

Steps 0 to 3 are done (cache and replay, midpoint windows, coverage guard, ending rule, `reason`, adaptive switch). The sweep (`devtools/sweep_join.py`, 64 configs from the cache, selection rule as written) did **not** reach the target, so Step 5 (defaults, live re-run) and the rest of Step 4 (hand-checked fixture) are on hold. Defaults in `config.py` are still the plan's starting values (`midpoint`, reach 200, guard 0.5, adaptive on).

Replay check: replaying the cache at the I1 settings reproduces I1's live label changes exactly (88 / 5 / 7 / 8 / 4 over 735 rows).

Selection set (6 recordings), top 10 by false-overrule rate (FOR) with coverage at least 85%. Held-out columns are reported, not used:

| Config | Sel FOR | Sel coverage | Sel errors (Lane A to final) | Held FOR | Held coverage |
|---|---|---|---|---|---|
| pad50 guard0.67 adaptive on | 3.85% | 86.97% | 44 to 49 | 4.90% | 91.32% |
| pad100 guard0.67 adaptive on | 4.17% | 91.24% | 44 to 51 | 4.25% | 94.63% |
| mid100 guard0.67 adaptive on | 4.17% | 91.24% | 44 to 51 | 4.25% | 94.63% |
| pad150 guard0.67 adaptive on | 4.38% | 92.31% | 44 to 52 | 4.21% | 95.45% |
| pad200 guard0.67 adaptive on | 4.38% | 92.31% | 44 to 52 | 5.14% | 95.45% |
| mid150 guard0.67 adaptive on | 4.38% | 92.31% | 44 to 52 | 4.21% | 95.45% |
| mid200 guard0.67 adaptive on | 4.38% | 92.31% | 44 to 52 | 5.14% | 95.45% |
| mid300 guard0.67 adaptive on | 4.64% | 92.31% | 44 to 53 | 5.14% | 95.45% |
| pad50 guard0.67 adaptive off | 8.24% | 86.97% | 44 to 65 | 10.29% | 91.32% |
| pad100 guard0.67 adaptive off | 8.85% | 91.24% | 44 to 69 | 9.91% | 94.63% |

I1 baseline (pad 50, no guard, adaptive on): selection FOR 14.84% (coverage 97.0%), held-out 12.22% (98.4%).

**Chosen by the rule:** `mid100 guard0.67 adaptive on`: selection FOR 4.17%, coverage 91.24%; **held-out FOR 4.25%, coverage 94.63%** (9 of 212 judged `match` rows, all in the two `marian_came` recordings). Target (held-out FOR at most 2% with coverage at least 85%) **not met**. No config in the grid has held-out FOR at or under 2% (best 4.21%), so it isn't a selection artefact.

What the sweep shows:
- The window rule is not the main lever any more: `pad100` to `pad200` and `mid100` to `mid300` all land within 0.5 points of each other (midpoint ties `pad100`; it wins only on the tie-break). Most of the I1 gain (15% to about 4 to 5%) comes from the coverage guard.
- The guard is the lever and 0.67 is the top of the grid: FOR is flat from guard 0 to 0.5 (10.6%, 9.7%, 9.7% at mid200) and drops to 4.4% at 0.67, at about 6 points of coverage. The grid does not show where it stops helping.
- The adaptive overrule matters a lot (FOR doubles with it off), the opposite of Q3's expectation: it can't be removed.
- Counted errors still rise over Lane A alone (44 to 51 on the selection set, 16 to 20 held-out), and clears (SUB or MISPRON to match) are only 9 and 5.

The 25 remaining false overrules at the chosen config (13 selection, 9 held-out `marian_came`, plus 3 `ring_ring`) fall into three patterns:
1. **Short function words with 40 to 160 ms windows and garbage heard** (`we` heard /i h/, `a` /h/, `for` /v ɛ/ and /w ɛ/, `you` /u ɡ/, `her` /ɹ ɚ/, `she` /s ʊ/, `up` /p ɛ/ in 60 ms, `guess`): about 11 rows. A phoneme from a neighbour or noise lands in the window, or the recogniser misses the vowel.
2. **Longer words with real divergence** (`market` /m aɪ d ə t/, `vegetables` /b ɹ ɪ k s ə ɡ ɚ z/, `string` /t ɪ ŋ …/, `shrimp`, `sprouting`, `happily`, `marian`, `sure`, `after`, `stall`, `horn`, `stayed`, `beans`): about 13 rows. Many are probably true mispronunciations that CrisperWhisper normalised to the right word (Q4), so some of this FOR is not false. Labels (B6b) are the only way to tell.
3. **A repeated word merged into one row** (`ring` heard /ɹ æ ŋ ɹ æ ŋ/, d 1.16): 1 row.

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
