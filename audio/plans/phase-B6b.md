# Phase B6b: Calibrate Lane B against hand labels

Status: approved 2026-11-03. All open questions resolved: the recommendation stands for each (Q3: two/"to" is relabelled match).

## Why

B6a tuned the join and the guard without labels, so it couldn't tell whether Lane B improves on Lane A. Lane B is off by default (`AUDIO_PHONETIC_ENABLED=false`) until that is shown. The user has now hand-labelled all 735 rows of the 9 real recordings (`labels.csv`, from `devtools/make_label_csv.py` on `feature/label-csv`). This phase measures both lanes against those labels, picks Lane B's settings on the selection recordings, checks them on the held-out ones, and decides whether Lane B is switched on.

Out of scope: retraining or replacing the recogniser, changes to Lane A's ASR or tagging (problems found there are reported to the Lane A developer, see "Lane A findings"), and the four audio flags.

## What the labels already show (planning analysis, 2026-11-03)

These numbers compare the `lane_a_tag` and `lane_b_tag` columns with `label`, using the current defaults. A row is a **counted error** if its tag is one of `COUNTED_TAGS`; the one row labelled `transposition` counts as an error too.

- **Labels:** 665 match and 48 true counted errors, of which 33 are in selection and 15 held-out. 8 rows are marked `unsure` in `notes`.

**Counted errors against the labels**

| | Errors found (TP) | False errors (FP) | Missed (FN) | Exact tag |
|---|---|---|---|---|
| Lane A alone | 45 | 20 | 3 | 706 / 735 |
| Lane A + Lane B (current) | 42 | 19 | 6 | 708 / 735 |
| Lane A + Lane B, never overruling a Lane A `match` (simulated) | 41 | 10 | 7 | 716 / 735 |

| Held-out only | TP | FP | FN |
|---|---|---|---|
| Lane A alone | 14 | 4 | 1 |
| Lane B (current) | 14 | 3 | 1 |
| Lane B, no match overrule | 13 | 0 | 2 |

**What this means**
1. **Lane A over-counts.** It reports 65 errors where the labels have 48. Most of its false errors are the ASR spelling a correctly read word differently: kris→"chris" ×3, mang→"mon"/"mom", cheese→"geez", tinoys→"tenoys", juanings→"huanings", mongo→"mango", and taho→"tofu". Lane B correctly clears most of these by sound. That is the job it was built for.
2. **Overruling a Lane A `match` doesn't work.** Lane B turned 10 `match` rows into mispronunciations. 9 of them are wrong (stayed, string, beans, shrimp, happily, marian, every, after, horn) and 1 is right (sprouting).
   - The wrong ones have `d_exp` 0.46–0.78, and the right one has 0.53, so no threshold separates them.
   - The child who said "dʒuanings" (a real mispronunciation) has `d_exp` 0.31 and wasn't overruled.
   - With this recogniser, Lane B's judgment on words the ASR heard correctly is noise.
3. **Lane B's misses are threshold cases:**
   - jacky→"jack" at exactly `d_exp` 0.25 = `T_FLAG`. kris→"chris" also scores 0.25 and is labelled match, so both sit on the same value.
   - perez→"pires" at 0.175.
   - two→"to" (homophones, Q3).
   - on→"in" (unsure).

**The two cases raised during labelling**
- **"juanings" pronounced "huanings"** (`everysaturbc0e…` row 17). The lexicon is right (`juaning: h w a n i ŋ`, with possessive handling). But the ASR wrote "huanings", so Lane A tagged a mispronunciation. The recogniser heard `m aɪ n ɪ ŋ`, missing the word-initial /h/, so `d_exp` is 0.29, above `T_FLAG`, and Lane B kept the error. In the other recording (`…9994` row 17), the child said "dʒuanings", which is a real mispronunciation, and both lanes said match. A weak /h/ in the recogniser can't be fixed by thresholds. This is recorded as a known limit.
- **"taho, taho" read but not recorded** (`mamawas…` rows 36 and 37). CrisperWhisper produced no words for either, so Lane A made 2 omissions. Lane B can't rescue them: in those gaps it heard nothing, and then only `t a`. Later in the same recording, taho→"tofu" is the same ASR problem: Filipino words outside the ASR's vocabulary. This is a Lane A / ASR issue (see "Lane A findings").

## Branch and data

- Branch `feature/b6b` from `feature/label-csv`. That branch has the CSV generator and sits on `feature/lane-b-integration`. Work in the existing worktree `D:\dev-cache\wt-label-csv` (switch it to the new branch) or a new worktree on D:. Don't disturb the main tree's branch. The B6b PR goes to `dev` after the Lane B integration PR merges.
- Labels (Q1): copy `C:\Users\choc\Downloads\new_labels - labels.csv (1).csv` to `audio/tests/fixtures/labels_b6b.csv` with these columns: `recording, split, row, passage_index, passage_word, spoken_word, label, notes`, plus `lane_a_tag` and `lane_b_tag` for reference. Drop `clip` (a local path) and the phoneme columns, because replay regenerates them. Apply the Q3 relabel if approved, and record it in the plan's Results.
- Replay uses the cached runs (`devtools/out/cache/`, no models). Rows are joined to labels by `(recording, row)`, and the session checks that `passage_word` matches on every row. A mismatch stops the run.

## Changes

| File | Change |
|---|---|
| `src/audio/config.py` | New `OVERRULE_MATCH: bool`, default per Q2. When false, Lane B never changes a row Lane A tagged `match`: its evidence is still attached, but the tag stays. `T_FLAG`, `T_OVERRULE` and `COVERAGE_GUARD_RATIO` change only if the sweep selects new values. Add `T_FLAG_INCLUSIVE: bool` only if the sweep shows `<` vs `<=` matters (jacky/kris sit on exactly 0.25). |
| `src/audio/arbitration/arbiter.py` | Honour `OVERRULE_MATCH`, setting `phonetic.reason = "match_kept"` (Q4). This is Lane B's file. |
| `devtools/score_labels.py` (new) | Replays every config in the grid over the 9 recordings and scores each against the labels. Output per config, per split: TP/FP/FN of counted errors, exact-tag agreement, counted errors per recording against the labelled count, and the same with the `unsure` rows excluded. Writes `devtools/out/b6b_sweep.csv` and a short markdown summary. |
| `tests/test_labels_b6b.py` (new) | Regression test using the chosen defaults: replay (skipped without the cache) must give FP and FN at most the values recorded in Results, per split. |
| `tests/fixtures/handchecked_i1.yaml` | The labels decide the expected tags: cups/"cops" and mongo/"mango" are **match** (the current Lane B result is right), and jacky/"jack" is **mispronunciation**. Turn the strict xfails into normal assertions where the new defaults pass, and keep any still-failing one as xfail with a plain reason. |
| `contracts/audio-service.md` | Add `match_kept` to the documented `phonetic.reason` values. That's an additive field value; ask before deciding whether it bumps the version (v7) (Q4). |
| `../compose.yaml` | Only if Lane B is switched on (Q5): change `AUDIO_PHONETIC_ENABLED: ${AUDIO_PHONETIC_ENABLED:-false}` to `true`. Otherwise compose keeps forcing it off, whatever `config.py` says. |
| Docs | Plan Results, `audio/README.md`, `docs/deployment-architecture.md` (Lane B on/off, VRAM, startup time), the root README "Current state", and the ROADMAP row. |

## Sweep and selection

- **Grid** (replay only, a few seconds per config):

  | Setting | Values |
  |---|---|
  | `OVERRULE_MATCH` | false, true |
  | `T_FLAG` | 0.15, 0.20, 0.25, 0.30, each with `<` and `<=` |
  | `T_OVERRULE` | 0.35, 0.45, 0.55 |
  | `COVERAGE_GUARD_RATIO` | 0.8, 1.0 (only when `OVERRULE_MATCH` is true) |

  Join, short-word and adaptive settings stay at the B6a values.
- **Selection rule, applied to the 6 selection recordings only:**
  1. Fewest FP + FN.
  2. Tie-break: fewer FN.
  3. Then higher exact-tag agreement.
  4. Then the setting closest to the current defaults.
- **Report:** the held-out numbers for the chosen config, and how many configs beat Lane A alone on held-out FP + FN (robustness, as in B6a).
- **Decision rule for switching Lane B on (Q5):** the chosen config must beat Lane A alone on FP + FN **on both splits** and not lose on exact-tag agreement. If it fails, Lane B stays off and the plan's Results record why.

## Lane A findings (report only, no changes)

Write these up as a short section in Results that the user can forward to the Lane A developer:
- **Filipino words missing from ASR output:** "taho, taho" dropped completely, and taho heard as "tofu". Possible fix on their side: pass the passage words to CrisperWhisper as an initial prompt or hotwords. That's their call.
- **Spelling variants counted as errors:** kris/"chris" ×3, mang/"mom"/"mon". `sound_key` might cover these.
- **A word not said but transcribed:** `ringringra…` row 9, "not", labelled omission. Possibly ASR filling in from context.

## Tests and checks

- `uv run pytest` passes, including the new label regression and the updated hand-checked fixture.
- Ruff and pyright are clean on new and changed files (Lane A's ruff debt is pre-existing; leave it).
- A replay with the chosen defaults reproduces the sweep row exactly.
- If Lane B is switched on: one live run on the 9 recordings (GPU) matches replay with 0 differing tags, as in B6a.

## Done when

- The sweep, the chosen config with selection and held-out numbers, the switch-on decision and the Lane A findings are recorded in Results.
- Defaults are updated, docs match, and B6b is marked done in the ROADMAP.
- Commits use Conventional Commits with no trailers. Don't push.

## Open questions (all resolved 2026-11-03: the recommendation stands for each)

- **Q1. Commit the labels?** Recommend yes, as `tests/fixtures/labels_b6b.csv` with no clip paths, phonemes or audio. It's only words already in the passages and transcripts, and the regression test needs it. Alternative: keep it git-ignored, in which case the regression test only runs on this machine.
- **Q2. `OVERRULE_MATCH` default.** Recommend **false**. The labels show 9 of 10 match overrules were wrong and no threshold separates them, and the simulation halves false errors (20 → 10, held-out 4 → 0) at a cost of 1 missed error (sprouting). The sweep includes `true` so this gets confirmed, not assumed. Lane B then works as a **repair** step: it clears Lane A's false errors by sound and adjusts the tag type, but never adds an error to a word the ASR heard as written.
- **Q3. two/"to"** (`mamawas…` row 42, labelled mispronunciation). "two" and "to" sound the same, so no audio-based system can tell them apart, and a teacher listening can't either. Recommend relabelling it as **match**. Your call: it's your label.
- **Q4. `phonetic.reason = "match_kept"`.** Recommend adding it so a kept match is visible in the evidence. The field is optional and documented as a set of values, so adding one is additive. Recommend bumping audio-service.md to v7, per the contract rule that any shape change bumps.
- **Q5. Switch Lane B on by default if the decision rule passes?** Recommend yes: `config.py` default true and the compose default true. On the A10G it adds about 20 s to startup, 1–2.5 s per request and about 1.5 GB of GPU memory, all within budget. Tell the deploying teammate before merging. Alternative: keep it off and let them set the env var.
- **Q6. `unsure` rows (8).** Recommend keeping them in the main score and reporting the without-unsure numbers next to it. If the chosen config changes when they're excluded, stop and report.
- **Q7. Small sample.** 48 true errors from one labeller, who saw both lanes' guesses (pre-filled when they agreed). Recommend stating this limit in Results and in the PR. The numbers support "Lane B helps as a repair step", not a precise accuracy claim.

## Plan change (2026-11-03): selection when the unsure rows change the pick

With the unsure rows the rule picked T_FLAG 0.30; without them, T_FLAG 0.25 (both OVERRULE_MATCH=false, T_OVERRULE 0.45, guard 1.0). They tie on FP+FN without the unsure rows (11 each across both splits) and differ by 1 row with them. The user chose T_FLAG 0.25 with `<`:
- When the two picks are within 1 row, prefer the config closer to the defaults and with fewer FN.
- A repair-only Lane B should not clear real errors.
- jacky/"jack" sits at exactly 0.25 and is labelled a mispronunciation.
Record both configs' numbers in Results.
