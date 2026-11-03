# Phase F1: The four audio flags

Status: approved 2026-11-03. All open questions resolved: the recommendation stands for each. The definitions stay subject to the team's answer (Q1); a changed definition becomes a Plan change.

## Why

The BE response carries `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous` and `is_hardly_audible`. The audio worker always returns `false` for them (`worker/mapping.py`), so the BE never gets a real value. No contract or spec defines them: `be-mai-openapi.yaml` gives only the names, and `audio-service.md` lists them as "in the SE plan, not in this response yet". They match items on the Phil-IRI Oral Reading Observation Checklist, which a teacher ticks yes/no while listening.

This phase proposes a measurable definition for each flag, builds them as tunable settings, and checks them on the 9 recordings. **The definitions are a proposal: the team confirms them (Q1) before this ships.**

Out of scope:
- `is_lack_expression` and `is_finger_pointing`, which the video lane owns.
- `wpm`, which already exists.
- Contract changes. The flags are already booleans in both contracts.

## Proposed definitions

Each flag is a yes/no for the whole reading, like the teacher's checklist. When there isn't enough evidence (too few timed words, too few punctuation marks), the flag is `false` and the worker logs why (Q4).

### `is_word_by_word`: choppy reading, a pause after nearly every word

- **Inputs:** Lane A rows in passage order. Take each pair of consecutive rows where both are timed and read (`match`, `mispronunciation`, `substitution`, `repetition`, `self_correction`), and both sides lie inside the same sentence with no passage punctuation between them. These are the **within-phrase gaps**.
- **Gap** = next row's `start` − this row's `end`. Pairs next to an omission, insertion, filler, false start or off-text row are skipped, because the pause there comes from the miscue, not the phrasing.
- **Flag** when at least `WBW_MIN_SHARE` (0.5) of the within-phrase gaps are at least `WBW_GAP_MS` (250 ms), and there are at least `FLAGS_MIN_GAPS` (10) gaps.
- **Why this measure:** a fluent reader runs words together, so most within-phrase gaps are near 0 ms with forced alignment. A word-by-word reader leaves a clear gap after most words. A slow but phrased reader has a low `wpm` without this pattern, so `wpm` alone isn't used.

### `is_disregard_punctuation`: no pause at full stops and commas

- **Inputs:** the passage text, with punctuation, kept next to Lane A's tokens. Walk `text.split()` and `normalize()` in the same order as `tokenize_passage`. A raw token ending in `. ! ? ;` marks a **sentence boundary** after its last word, and `, :` marks a **comma boundary**. This keeps the passage indices identical to Lane A's.
- At each boundary where the words on both sides were read and timed, take the gap as above.
- **A boundary is honoured** when the gap is at least `PUNCT_PAUSE_MS` (sentence 300 ms, comma 150 ms) **and** at least `PUNCT_REL` (1.5) × the reader's median within-phrase gap. The second condition stops a word-by-word reader, who pauses everywhere, from counting as observing punctuation just because every gap is long.
- **Flag** when fewer than `PUNCT_MIN_SHARE` (0.5) of the sentence boundaries are honoured, with at least `FLAGS_MIN_BOUNDARIES` (3) sentence boundaries.
- Commas are measured and reported, but only sentence boundaries decide the flag at first (Q3).

### `is_monotonous`: flat pitch, little intonation

- **Inputs:** the WAV and the word times. Run a pitch track (F0) at 10 ms frames, range 75–600 Hz, because children's voices are high (Q2 for the tracker).
- Take the **median F0 per read word** (frames inside the word's span and voiced). Using per-word medians keeps pitch-tracker jitter and octave errors from counting as expression. Drop words whose median is more than 1 octave from the recording's median.
- Convert to semitones relative to the reader's median, and take the **standard deviation** across words.
- **Flag** when SD < `MONO_MAX_ST` (2.0 semitones), with at least `FLAGS_MIN_WORDS` (15) voiced words.
- Reading aloud with normal intonation usually varies by about 3–5 semitones (SD); a flat voice is under about 2. The threshold is checked on the 9 recordings.

### `is_hardly_audible`: too quiet to hear well

- **Inputs:** the WAV and the word times.
  - **Speech level:** RMS in dBFS over the read words' spans.
  - **Noise level:** RMS over the gaps (no word, at least 200 ms long), 10th percentile of 50 ms frames.
  - **SNR** = speech level − noise level.
- **Flag** when speech level < `AUDIBLE_MIN_DBFS` (−35 dBFS) **or** SNR < `AUDIBLE_MIN_SNR_DB` (10 dB).
- The SNR condition matters because browsers often apply automatic gain control. A quiet child gets boosted to a normal level, and the room noise is boosted with them, so the voice stands out from the noise less. Whether the FE leaves gain control on changes which condition does the work (Q5).

## Where the code goes

- **New package `src/audio/flags/`:**
  - `fluency.py`: word-by-word and punctuation, from rows and passage text.
  - `acoustics.py`: pitch and loudness, from the WAV array and word times.
  - `__init__.py`: `compute_flags(wav, rows, passage_text, settings) -> AudioFlags`.
  - It returns the four booleans plus a `metrics` dict: gap share, honoured share, pitch SD, speech dBFS, SNR, and the evidence counts. The metrics are logged and used by the devtool.
- **Worker** (`worker/app.py`, `worker/mapping.py`):
  - After the pipeline runs, call `compute_flags` in the same thread. The WAV array is already loaded by the `load_wav` check, so it is reused rather than read twice.
  - Pass the flags into `to_worker_result`. Remove the "not implemented" startup log.
- **Lane A's code isn't touched.** The flags read `AudioResult["words"]` as it is. They aren't added to `AudioResult` or `audio-service.md` (Q6).
- A flag failing must never fail the request. Any exception is logged and that flag returns `false`.

## Settings (`AUDIO_` prefix, all provisional until checked)

| Setting | Default |
|---|---|
| `FLAGS_ENABLED` | true (false = all four `false`, the old behaviour) |
| `WBW_GAP_MS`, `WBW_MIN_SHARE` | 250, 0.5 |
| `PUNCT_SENTENCE_PAUSE_MS`, `PUNCT_COMMA_PAUSE_MS`, `PUNCT_REL`, `PUNCT_MIN_SHARE` | 300, 150, 1.5, 0.5 |
| `MONO_MAX_ST`, `PITCH_FLOOR_HZ`, `PITCH_CEIL_HZ` | 2.0, 75, 600 |
| `AUDIBLE_MIN_DBFS`, `AUDIBLE_MIN_SNR_DB` | −35, 10 |
| `FLAGS_MIN_GAPS`, `FLAGS_MIN_BOUNDARIES`, `FLAGS_MIN_WORDS` | 10, 3, 15 |

## New dependency (Q2)

`praat-parselmouth`, Praat's pitch and intensity analysis as a Python wheel:
- CPU only, about 15 MB, with Linux and Windows wheels.
- Under 0.5 s for a 60 s recording.
- It's the standard tool in speech research, and its pitch tracker handles children's voices with the 75–600 Hz range.
- Pin it in `pyproject.toml`/`uv.lock`. The image needs no system package.

## Checking on real recordings

- **New `devtools/flag_report.py`:**
  - Runs from the cached runs (`replay.py`, no models) plus the WAVs.
  - Writes `devtools/out/flags.md`: per recording, every metric, every flag, and the user's yes/no.
- **The user's yes/no for each flag on the 9 recordings** (about 10 minutes, Q7) goes in `tests/fixtures/flags_labels.csv` with columns `recording, word_by_word, disregard_punctuation, monotonous, hardly_audible, notes`.
- **Synthetic checks for `is_hardly_audible`.** The 9 recordings are probably all audible, so the report adds copies of 2 recordings:
  - attenuated by 20 and 30 dB
  - attenuated by 20 dB with noise mixed back in at the original noise level (what gain control produces)
  These are generated in memory, not saved.
- **Adjusting thresholds:** if a flag disagrees with the user's labels and moving a threshold within a sensible range fixes it without breaking another recording, change the default and record it. Otherwise keep the default and record the disagreement. With 9 recordings this is a sanity check, not calibration (Q7).

## Tests (fast suite, no models)

- **`tests/flags/test_fluency.py`** (made-up rows):
  - Fluent rows (gaps about 0) → not word-by-word. Every gap 400 ms → word-by-word.
  - Gaps next to omissions and insertions are skipped.
  - Too few gaps → false, with the reason "insufficient".
  - Punctuation boundaries come from the raw text, and the indices match `tokenize_passage` (hyphens, numbers, possessives).
  - A word-by-word reader with no extra pause at full stops → disregard true. A reader who pauses at full stops → false.
- **`tests/flags/test_acoustics.py`** (synthetic signals):
  - A constant 220 Hz tone in word spans → monotonous. A tone gliding ±4 semitones across words → not.
  - An octave jump on one word is dropped.
  - Tone at −20 dBFS over −60 dBFS noise → audible. −45 dBFS → hardly audible. −20 dBFS over −25 dBFS noise → hardly audible (SNR).
- **`tests/worker/`:**
  - The flags reach the response.
  - `FLAGS_ENABLED=false` → all false.
  - An exception inside `compute_flags` → that flag is false and the request still returns 200.
- A test that `compute_flags` on a 60 s synthetic signal takes under 1 s.

## Done when

- `uv run pytest` passes. Ruff and pyright are clean on new and changed files.
- `devtools/out/flags.md` is produced. The plan's Results record, per recording, the metrics, the flags, the user's labels and any threshold change.
- One live request through the worker on the dev GPU returns the four flags. Record the added time (expected under 1 s).
- `audio/README.md` gets a plain-language "Audio flags" section, written so it can go to the team: each flag's definition, its threshold and its limits.
- The root README "Current state", the ROADMAP row and `docs/deployment-architecture.md` are updated if timing changes.
- Commits use Conventional Commits with no trailers. Don't push.

## Branch

`feature/audio-flags` from `feature/audio-worker`, because the worker code lives there. Open the PR only after #27 merges, with base `dev`, so the stack doesn't get deeper.

## Open questions (all resolved 2026-11-03: the recommendation stands for each)

- **Q1. Team sign-off on the definitions.** Recommend sending the team the four definitions above as a short message now, and implementing in parallel because every threshold is a setting. Ideally a teacher confirms what "word by word" and "disregards punctuation" mean in practice. If the team changes a definition, not just a number, it becomes a Plan change.
- **Q2. Pitch tracker.** Recommend `praat-parselmouth`. Alternatives: `librosa.pyin` (good, but pulls in numba and slows the first call) or a hand-written YIN in numpy (no new dependency, but more code to get right).
- **Q3. Commas.** Recommend measuring them and reporting them in metrics, but deciding the flag on sentence ends only. Children often don't pause at commas, and teachers mostly mean full stops. Add commas later if the team wants.
- **Q4. Not enough evidence.** Recommend `false` plus a log reason. The contract has no "unknown" value, and a false "yes" is worse for a child than a missed one.
- **Q5. Automatic gain control in the FE.** Ask the FE developer whether `getUserMedia` uses `autoGainControl` (on by default in browsers). Recommend keeping both conditions (level or SNR) either way. If gain control is on, the SNR condition does most of the work.
- **Q6. Flags in `AudioResult`?** Recommend no: compute them in the worker so Lane A's files and `audio-service.md` don't change. If the Lane A developer wants them in the pipeline result later, that's a contract change (v8).
- **Q7. Labels and sample size.** Recommend the user marks yes/no per flag per recording (36 judgements) before the threshold check. With 9 recordings, which are probably mostly fluent and audible, this catches clearly wrong definitions but can't fine-tune thresholds. Say so in the README and the PR, and collect more recordings for a later check.

## Results

Run 2026-11-03 at the defaults above, on the 9 cached recordings (Lane A + Lane B, as the worker runs), with `devtools/flag_report.py`. Labels are the user's yes/no in `tests/fixtures/flags_labels.csv`; "unsure" is the user's note. The same flags and metrics came out of the live worker (below), so replay and worker agree.

### Per recording: flag (label); a star marks a disagreement

| recording | word_by_word | disregard_punctuation | monotonous | hardly_audible | note |
|---|---|---|---|---|---|
| amouseanda0d17 | no (yes) * | no (no) | no (no) | no (no) | |
| everysatur88f3 | no (no) | no (yes) * | yes (yes) | no (no) | |
| everysatur9994 | no (no) | no (yes) * | yes (yes) | no (no) | unsure |
| everysaturbc0e | no (no) | no (no) | no (yes) * | no (no) | |
| mamawasfeef5f | no (yes) * | no (yes) * | yes (yes) | no (no) | |
| mariancame60bc | no (no) | no (no) | no (no) | no (no) | |
| mariancamea8fe | no (no) | no (yes) * | no (no) | no (no) | unsure |
| nickisatrifc65 | no (yes) * | no (no) | no (no) | no (no) | |
| ringringraf85a | no (no) | yes (no) * | no (no) | no (no) | |

9 disagreements of 36: word_by_word 3 (all missed), disregard_punctuation 5 (4 missed, 1 false yes), monotonous 1 (missed), hardly_audible 0.

### Per recording: metrics

| recording | wbw gaps | share of gaps >= 250 ms | median gap ms | sentence ends | honoured share | comma honoured share | pitch SD (st) | voiced words | speech dBFS | noise dBFS | SNR dB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| amouseanda0d17 | 66 | 0.29 | 140 | 9 | 0.78 | 0.50 (4) | 2.73 | 77 | -23.9 | -74.6 | 50.7 |
| everysatur88f3 | 54 | 0.09 | 60 | 7 | 1.00 | 0.12 (8) | 1.34 | 78 | -21.5 | -120 | 98.5 |
| everysatur9994 | 61 | 0.13 | 80 | 7 | 0.86 | 0.30 (10) | 1.83 | 78 | -20.0 | -51.2 | 31.1 |
| everysaturbc0e | 61 | 0.05 | 60 | 7 | 0.71 | 0.40 (10) | 2.32 | 80 | -21.1 | -54.6 | 33.5 |
| mamawasfeef5f | 46 | 0.24 | 100 | 10 | 0.70 | 0.71 (7) | 1.92 | 67 | -22.5 | -62.3 | 39.8 |
| mariancame60bc | 63 | 0.06 | 80 | 10 | 0.90 | 0.25 (4) | 2.64 | 80 | -19.1 | -68.8 | 49.6 |
| mariancamea8fe | 63 | 0.17 | 80 | 10 | 0.60 | 0.75 (4) | 3.20 | 76 | -23.8 | -48.1 | 24.3 |
| nickisatrifc65 | 54 | 0.17 | 100 | 11 | 0.91 | 0.86 (7) | 2.37 | 78 | -22.3 | -70.2 | 47.8 |
| ringringraf85a | 43 | 0.00 | 60 | 11 | 0.45 | 0.75 (4) | 4.63 | 69 | -10.3 | -120 | 109.7 |

(`-120` is digital silence in the gaps: the floor of the scale.) Full-precision numbers: `devtools/out/flags.md` after `uv run python devtools/flag_report.py`.

### Threshold check

No default changed. Each setting was swept over a range on the 9 recordings (a throwaway script; the same can be done with `flag_report.py --set NAME=VALUE`):

- **`is_hardly_audible`**: no disagreement, nothing to change.
- **`is_monotonous`** (one missed: `everysaturbc0e`, SD 2.32; the nearest "no" label is `nickisatrifc65`, SD 2.37). `MONO_MAX_ST` 2.35 would give 0 disagreements, but the margin is 0.05 semitones, which is smaller than the measurement noise, and 2.5 already breaks `nickisat`. Kept 2.0. A flat voice at 2.0 to 2.4 semitones is a judgement call; one more labelled recording would settle it.
- **`is_word_by_word`** (3 missed, shares 0.29, 0.24 and 0.17 against 0.00 to 0.17 for the "no" recordings). Only two settings give 0 disagreements: `WBW_MIN_SHARE` 0.2 with `WBW_GAP_MS` 200, and `WBW_GAP_MS` 100 with `WBW_MIN_SHARE` 0.5. The first means "one gap in five is a pause", which is not "a pause after nearly every word"; the second counts 100 ms as a pause, but a fluent reader's median gap here is 60 to 80 ms and the timestamps are rounded to 20 ms. Neither is a sensible threshold for the definition, so kept 250 ms / 0.5. The labels suggest the person's idea of "word by word" is milder than the definition. This is a Q1 question for the team, not a number to tune.
- **`is_disregard_punctuation`** (5 disagreements). No combination of `PUNCT_SENTENCE_PAUSE_MS` (200 to 400), `PUNCT_REL` (1.0 to 2.0) and `PUNCT_MIN_SHARE` (0.3 to 0.8) gives fewer than 4. Three recordings the person marked "disregards" (`everysatur88f3`, `everysatur9994`, `mamawas`) have 70 to 100% of their full stops followed by a pause of 300 ms or more, so the person was probably listening for something else (commas, the rise and fall of the voice at a stop, or the long pauses elsewhere). `PUNCT_MIN_SHARE` 0.4 would fix `ringringraf85a` (honoured share 0.45) and break nothing, but it leaves the other four wrong and only moves the one recording near the line, so it was not changed. The definition needs review with a teacher (Q1).

### Quiet copies for `is_hardly_audible` (in memory; the original is audible)

| copy | flag | speech dBFS | noise dBFS | SNR dB |
|---|---|---|---|---|
| mamawasfeef5f, -20 dB | yes | -42.5 | -82.3 | 39.8 |
| mamawasfeef5f, -30 dB | yes | -52.5 | -92.3 | 39.8 |
| mamawasfeef5f, -20 dB + original noise (-62.3 dBFS) | yes | -42.5 | -62.3 | 19.9 |
| ringringraf85a, -20 dB | no | -30.3 | -120 | 89.7 |
| ringringraf85a, -30 dB | yes | -40.3 | -120 | 79.7 |
| ringringraf85a, -20 dB + original noise (-120 dBFS) | no | -30.3 | -120 | 89.7 |

`ringringraf85a` is a very loud recording (-10 dBFS), so 20 dB down is still -30 dBFS, above the -35 threshold; it is flagged at 30 dB down. The "gain control" copies keep the SNR at 19.9 dB for `mamawas` and 89.7 dB for `ringring` (its original noise is digital silence), so both stay above the 10 dB limit and the SNR condition never fired on these copies. It is covered by the synthetic test (tone at -20 dBFS over -25 dBFS noise), but not yet by a real recording.

### Live check (worker on the dev GPU, GTX 1660 Ti)

`uv run uvicorn audio.worker.app:create_app --factory --port 8001`, ready in about 60 s including the first load. One `POST /v1/jobs` for `mamawasfeef5f` (44 s) returned 200 in 24.3 s (27.0 s the second time) with `is_word_by_word` false, `is_disregard_punctuation` false, `is_monotonous` true, `is_hardly_audible` false, the same as the replay. The flags took 85 ms and 78 ms (the `flags_ms` in the "flags computed" log), so they add about 0.1 s.

### Decisions made while building

- Rows without a passage index (a repetition) cannot be placed against the punctuation, so the pairs they form are skipped for both fluency flags. The plan listed `repetition` as a read tag; as a row it still counts for pitch and loudness.
- Words about an octave from the median (more than 11 semitones, not strictly more than 12) are dropped from the pitch spread, because a real octave error measures about 12 and the plan's "an octave jump is dropped" test requires it.
- `is_hardly_audible` needs at least one timed read word. Without a gap of 200 ms or more there is no noise estimate (`audible_reason=no_noise_estimate`) and the level alone decides.
- The flag metrics are logged as `flag_metrics` in the "flags computed" line (numbers only, no text).

