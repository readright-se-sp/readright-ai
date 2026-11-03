# Audio lane roadmap

The audio lane implements the GO2 pipeline (Architecture Specification Rev 2) behind the worker contract in [../contracts/worker-api.md](../contracts/worker-api.md). Work is split by owner:

| Part | Stages | Owner |
|---|---|---|
| Lane A (word lane) | 1 Normalise, 2 Align (Needleman-Wunsch), 3 Detect, 5 Post-pass | Lane A developer |
| Lane B (phonetic lane) | 0 Passage prep, 4a Recognise, Join, Compare, 4 Arbitrate | Lane B developer |
| Shared | Types, worker API, scoring and flags, Docker image | Lane B builds interim versions (Phase B0, B7); reconciled with Lane A when their code lands |

This file covers Lane B and the interim shared pieces. Lane A adds its own phases here when its code arrives.

## Status

| # | Phase | Status |
|---|---|---|
| B0 | Project setup and shared types (interim) | Done: [plans/phase-B0.md](plans/phase-B0.md) |
| B1 | Stage 0 passage preparation (espeak-ng IPA) | Done: [plans/phase-B1.md](plans/phase-B1.md) |
| B1.1 | Real passages and lexicon coverage | Done: [plans/phase-B1.1.md](plans/phase-B1.1.md) |
| B2 | Phoneme recogniser spike and implementation | Done: [plans/phase-B2.md](plans/phase-B2.md), spike: [plans/phase-B2-spike.md](plans/phase-B2-spike.md) |
| B3 | Join by time and coverage candidates | Done: [plans/phase-B3.md](plans/phase-B3.md) |
| B4 | Compare (weighted phoneme distance, accent table) | Done: [plans/phase-B4.md](plans/phase-B4.md) |
| B5 | Arbitration | Done: [plans/phase-B5.md](plans/phase-B5.md) |
| I1 | Integrate Lane B into Lane A on `dev` | Done on `feature/lane-b-integration` (PR to `dev` when the user says so): [plans/phase-I1.md](plans/phase-I1.md) |
| B6a | Join window, coverage guard and short-word rule on real ASR timings (no labels) | Done on `feature/lane-b-integration`: [plans/phase-B6a.md](plans/phase-B6a.md) |
| B6b | Thresholds, accent weights, near-equal vowels (needs teacher labels) | Done on `feature/b6b`: [plans/phase-B6b.md](plans/phase-B6b.md). Lane B is on by default |
| B6 | Calibration on real recordings | Split into B6a and B6b (section below is the original B6 outline) |
| I2 | Audio worker HTTP API and image | Done on `feature/audio-worker` (PR to `feature/lane-b-integration` or `dev` when the user says so; Docker image to be verified on EC2): [plans/phase-I2.md](plans/phase-I2.md) |
| B7 | Worker API, scoring and contract mapping (interim) | Superseded by Lane A + I1 + I2 |

## How to work a phase

1. **Before planning**, read: this file, [../CLAUDE.md](../CLAUDE.md), [the worker contract](../contracts/worker-api.md), [the BE contract](../contracts/be-mai-openapi.yaml) (miscue shape), [the deployment doc](../docs/deployment-architecture.md) (GPU, time budget), and the current code in `src/` and `tests/`.
2. **Draft a plan** in `plans/phase-<id>.md` (planning and implementation run in separate sessions, so the plan file is the hand-off): files to add or change, new settings, new dependencies, behavior, tests, done criteria, and the phase's open questions with a recommended answer for each. Do not resolve an open question silently.
3. **Wait** for the user's approval of the plan and an explicit go. Never start a phase, or the next one, unprompted.
4. **Implement** on the single branch `feature/lane-b` (no per-phase branches). Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, then commit with a Conventional Commit message and no Claude trailers. Do not push unless asked.
5. **Finish**: mark the phase done in the status table above and in the root README's "Current state"; record decisions made during the phase in its section below; update [../README.md](../README.md) and [../docs/deployment-architecture.md](../docs/deployment-architecture.md) if behavior changed.

## Pipeline and the Lane A / Lane B boundary

```
audio.wav ─┬─ Lane A: CrisperWhisper → Normalise → Align (NW) → Detect ──┐  Units + Stage 3 labels
           │                                                            ▼
           └─ Lane B: Phoneme recogniser (whole recording) ─────────► Join by time
Stage 0 passage prep (expected IPA, variants) ───────────────────────────┘
                                                                        ▼
                                          Compare → Arbitrate → WordVerdicts + final labels
                                                                        ▼
                                          Post-pass (Lane A) → Scoring → worker response
```

- CrisperWhisper is the ASR and the authority on word identity, boundaries and timings (agreed design; the Rev 2 spec's "WhisperX" is replaced by CrisperWhisper).
- Lane B decides how each word sounded, for every word (Rev 2: whole recording, not only flagged words).
- The recogniser starts as soon as `audio.wav` exists and runs in parallel with transcription. The lanes meet at Join.
- If Lane B fails or runs out of time, arbitration returns the Stage 3 labels unchanged and logs `phonetic_check=skipped`. The request does not fail. The contract has no field for this, so it is log-only.

## Target layout

```
audio/
├── src/audio/
│   ├── types.py                 # shared dataclasses (B0, interim)
│   ├── settings.py
│   ├── phonetics/               # B1, B4
│   │   ├── g2p.py               # espeak-ng via phonemizer, variants, cache
│   │   ├── lexicon.py           # hand-written Filipino proper nouns
│   │   ├── ipa.py               # symbol normalisation (model vocab ↔ espeak)
│   │   ├── accent_table.py
│   │   └── features.py          # articulatory-feature substitution cost
│   ├── recognition/             # B2
│   │   └── phoneme_recognizer.py
│   ├── lane_b/                  # B3, B4
│   │   ├── join.py
│   │   └── compare.py
│   ├── arbitration/             # B5
│   │   └── arbiter.py
│   ├── scoring.py               # B7 (interim)
│   ├── pipeline.py              # B7: stage wiring, Lane A stubs until merged
│   └── api/                     # B7: POST /v1/jobs, /health/ready, /stats
├── tests/fixtures/              # passages, short recordings, golden phonemes
├── pyproject.toml
└── Dockerfile                   # includes espeak-ng
```

---

## Phase B0 — Project setup and shared types (interim)

**Goal.** An `audio/` uv project and one module of shared types, so Lane B can be built and tested without Lane A code.

**Build.**
- `pyproject.toml` (Python 3.11+, ruff, pytest), `src/audio/` package, settings via pydantic-settings with an `AUDIO_` prefix.
- `types.py` (frozen dataclasses), named after the Rev 2 spec:
  - `Unit(surface, key, passage_span, source_indices, start_ms, end_ms, align_score, verifiable)`: one aligned passage position. `start_ms`/`end_ms` are `None` when there is no ASR timing.
  - `Stage3Label` enum: `CORRECT`, `SUBSTITUTION`, `MISPRONUNCIATION`, `OMISSION`, `INSERTION`.
  - `PhonemeToken(symbol, start_ms, end_ms, confidence)`.
  - `CoverageCandidate(start_ms, end_ms, phonemes, after_position)`.
  - `WordVerdict(expected_phonemes: list[list[str]], heard_phonemes: list[str], distance_expected, distance_hypothesis, deviations: list[tuple[str, str]], label, phonetic_check)`.
  - `LaneAResult(units, labels, trace)` and `ArbitrationResult(verdicts, coverage_candidates, duration_ratios)`: the two hand-off points.
- `tests/fixtures/`: 2–3 hand-built `LaneAResult` fixtures that match real recordings.

**Done when.** The project installs, the lint and test commands pass, and the types have round-trip tests.

**Migration note.** When Lane A pushes its code, diff their types against `types.py`, keep one definition, and update every Lane B import in a single change.

## Phase B1 — Stage 0 passage preparation

**Goal.** For each passage word, the list of accepted IPA pronunciations.

**Build.**
- `phonemizer` with the espeak-ng backend (`en-us`, `with_stress=False`, `preserve_punctuation=False`); one word per call, batched.
- Variants: espeak output plus a small variant table for function words (the: ðə/ði, a: ə/eɪ, to: tə/tu) and heteronyms (read, live, lead, wind, tear: both forms until there is context).
- `lexicon.py`: 30–60 hand-written entries for Filipino proper nouns and loanwords (Aling, Nena, Cebu, jeepney, pandesal, …). The lexicon takes priority over espeak.
- `ipa.py`: one normalisation applied to every phoneme sequence. It splits into segments, drops stress and syllable marks, removes tie bars, folds length marks, and maps rhotic vowels consistently.
- Cache key `sha256(normalised passage text)`, in-process LRU.
- The input word keys must match Lane A's normalised keys (number words, contractions, possessives). Until Lane A lands, use a minimal normaliser (lowercase, strip punctuation) and record the gap.

**Tests.** Golden IPA for about 50 words; variants; the lexicon overrides espeak; cache hit.

**Open questions.**
- Heteronyms: both variants now; use POS-based choice later. Recommendation: both, because it is simplest and errs toward not flagging the word.
- Store the lexicon in code or in `passages.proper_nouns` (spec §8.2; that column does not exist yet). Recommendation: a code-side YAML file now.

## Phase B2 — Phoneme recogniser

**Goal.** Run once over the whole WAV and get `list[PhonemeToken]` with times accurate to about 20 ms.

**Spike first (half a day, spec §8.4).** Compare on 3 real recordings:
- `facebook/wav2vec2-lv-60-espeak-cv-ft` (main candidate: about 300M parameters, espeak IPA output)
- `facebook/wav2vec2-xlsr-53-espeak-cv-ft` (multilingual pretraining, may handle accented speech better)

Measure:
- phoneme error rate against the expected passage phonemes on correctly read words
- timing drift against CrisperWhisper word boundaries
- wall-clock and peak VRAM on the A10G in fp16, with CrisperWhisper loaded at the same time
- whether the model's vocabulary maps cleanly onto `ipa.py`

**Build.**
- `PhonemeRecognizerProtocol.recognise(wav_path, deadline) -> list[PhonemeToken]`.
- Implementation: transformers CTC. Chunk long audio with overlap (for example 30 s windows with 1 s stride), take greedy argmax, collapse repeated frames and blanks, and keep each segment's frame span × 20 ms. Confidence is the mean softmax probability over the span.
- Load once at startup. `/health/ready` reports 503 until it is loaded.
- Check the deadline between chunks; past it, raise and let arbitration degrade to Stage 3 labels.

**Settings.** `AUDIO_PHONEME_MODEL`, `AUDIO_PHONEME_DEVICE`, `AUDIO_PHONEME_FP16`, `AUDIO_PHONEME_CHUNK_S`.

**Tests.** Unit tests on the CTC collapse with synthetic logits; one slow test on a fixture WAV, marked so CI can skip it.

**Done when.** The spike numbers are recorded below and a model is chosen.

**Result.** Chosen: `facebook/wav2vec2-xlsr-53-espeak-cv-ft`, fp32 (`AUDIO_PHONEME_FP16=false`). See [plans/phase-B2-spike.md](plans/phase-B2-spike.md).

## Phase B3 — Join by time

**Build.**
- Each `PhonemeToken` goes to the unit whose window `[start_ms − JOIN_PAD_MS, end_ms + JOIN_PAD_MS]` contains its midpoint. Overlapping windows: unpadded containment first, then the nearest edge, then the nearer centre, then the lower unit index.
- Phonemes outside every window are grouped into runs. A run of at least `COVERAGE_MIN_MS` becomes a `CoverageCandidate`, tagged with the passage position it follows. These feed the post-pass and the OMISSION rule in arbitration.
- Units with `verifiable=False` (no timing) get no phonemes.
- Duration per unit (`ms_per_phoneme`): `(end_ms − start_ms) / expected_count`, where `expected_count` is the sum of the shortest variant lengths over the unit's passage tokens. `None` for insertions, omissions and untimed units. Handed to the post-pass.
- Coverage runs also break on silence longer than `AUDIO_COVERAGE_MAX_GAP_MS`. Runs shorter than `COVERAGE_MIN_MS` go to `JoinResult.discarded`.

**Settings.** `AUDIO_JOIN_PAD_MS` (start at 80, try 50–100), `AUDIO_COVERAGE_MIN_MS` (start at 120, measure), `AUDIO_COVERAGE_MAX_GAP_MS` (start at 300, measure).

**Tests.** Boundary phonemes, overlapping windows, gaps that do and do not reach `COVERAGE_MIN_MS`, units without timing.

## Phase B4 — Compare

**Build.**
- Weighted Levenshtein over phoneme lists, with a backtrace.
  - Substitution cost: 0.1 for accent-table pairs; otherwise `0.4 + 0.6 ×` the articulatory-feature distance (panphon, scaled to 0–1, from the committed `feature_distances.json`; panphon is build-time only).
  - Insertion and deletion cost: 1.0.
  - Final-cluster simplification: deleting the last consonant of a word-final cluster costs about 0.2.
- `distance_expected = min over variants of cost / len(variant)`.
- `distance_hypothesis`: the same comparison against the IPA of CrisperWhisper's word (G2P on the fly, cached). `None` when the word matched.
- `deviations`: substitution, insertion and deletion pairs from the best backtrace, with accent-table pairs left out.
- Accent table (spec §6): f→p, v→b, θ→t, ð→d, z→s, ʒ→ʃ, ɪ↔i, ʊ↔u, æ→a/ɛ, ə → full vowel. Beyond the spec: eɪ→e/ɛ, oʊ→o, ɹ→ɾ. It is data in one file (`phonetics/data/accent_table.yaml`), so the weights can be calibrated in B6.

**Tests.** Identical sequences cost 0; an accent pair is near 0; cat→cap is above `T_FLAG`; the best variant wins; the backtrace gives the expected deviations.

**Open question.** Should θ→t be cost-free (spec §6.4)? Recommendation: low cost (0.1), not zero, then let calibration decide.

## Phase B5 — Arbitration

**Goal.** A final label per passage position from the Stage 3 label and the `WordVerdict`.

**Rules (spec Stage 4).** Thresholds must satisfy `T_FLAG < T_OVERRULE`.

| Stage 3 | Condition | Final |
|---|---|---|
| CORRECT | `d_exp ≤ T_OVERRULE` | CORRECT |
| CORRECT | `d_exp > T_OVERRULE` and deviations outside the accent table | MISPRONUNCIATION |
| SUB / MISPRON | `d_exp ≤ T_FLAG` | CORRECT |
| SUB / MISPRON | `d_hyp < d_exp` | SUBSTITUTION |
| SUB / MISPRON | far from both | MISPRONUNCIATION |
| SUB / MISPRON | no hypothesis distance (G2P failed or empty surface) | Stage 3 label |
| OMISSION | the unit's own heard phonemes are empty | OMISSION |
| OMISSION | the unit's own heard phonemes are close to the passage word (`d_exp ≤ T_FLAG`) | CORRECT |
| any | `verifiable=False`, phonetic check skipped, or a timed non-omission with no heard phonemes | Stage 3 label |

INSERTION passes through unchanged to the post-pass.

**Build.** A pure function `arbitrate(lane_a: LaneAResult, verdicts, candidates, thresholds) -> ArbitrationResult`. Distances are always recorded in the verdict, even when the label does not change.

**Settings.** `AUDIO_T_FLAG` (start at 0.25), `AUDIO_T_OVERRULE` (start at 0.45). Startup fails if `T_FLAG >= T_OVERRULE`.

**Tests.** One table-driven case per rule row; boundary values on both thresholds; the degraded path.

**Open question.** Abstention ("Uncertain", spec §11) is not selected. Recommendation: leave it out of v1. Log the cases where the label falls within ±0.05 of a threshold, so the data exists if abstention is revisited.

## Phase B6 — Calibration on real recordings

**Data.** Recordings in the team's Google Drive folder (link kept outside the repo; student audio never goes in git). Download them to a local, git-ignored `audio/data/` and keep the labels as CSV (`recording, word_index, passage_word, label`).

**Build.** `devtools/calibrate.py` runs the pipeline with the Lane A fixtures or real Lane A output. It grid-searches `T_FLAG`, `T_OVERRULE`, `JOIN_PAD_MS` and the accent weights, and reports per-type precision/recall, Cohen's κ against the teacher labels, and the Oral Reading Score error.

**Done when.** The chosen values are recorded below and set as setting defaults, and the per-type metrics are recorded.

**Open questions.**
- How many recordings, and are they word-level labelled? If not, labelling a held-out subset is part of this phase.
- Keep a held-out split so the thresholds are not tuned on the evaluation set.

## Phase B7 — Worker API, scoring and contract mapping (interim)

**Goal.** A runnable `audio-worker` that meets [worker-api.md](../contracts/worker-api.md), using Lane A stubs until Lane A's real code lands.

**Build.**
- FastAPI app: `POST /v1/jobs`, `GET /health/ready`, `GET /stats`.
  - Echo `job_id`; drop expired jobs with 504 `DEADLINE_EXCEEDED`.
  - `MEDIA_UNREADABLE` for an unreadable WAV; `NO_SPEECH` when the recogniser and ASR find no speech.
- `pipeline.py`: Stage 0 and the recogniser run in parallel with Lane A, then Join → Compare → Arbitrate → post-pass (stub: pass-through) → scoring.
- `scoring.py`: Oral Reading Score = (passage words − counted miscues) / passage words × 100. The denominator is the raw passage tokens. Self-correction is not counted. The result is clamped to 0–100.
- Miscue mapping to the contract:
  - `actual_phoneme` is the best expected variant; `spoken_phoneme` is the heard phonemes, both IPA.
  - `spoken_phrase` and `spoken_phoneme` are empty for an omission.
  - Omission timing: `start_ms` = end of the previous timed unit, `end_ms` = start of the next timed unit, or `start_ms` if they touch or one is missing. This spans the gap where the word should have been read.
- `wpm`, `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous` and `is_hardly_audible` are not in the Rev 2 spec. Interim: compute `wpm` from the first and last word times; return `false` for the four flags and log that they are not implemented. Each flag needs its own definition before it is built.
- Dockerfile: CUDA base, `espeak-ng` system package, model weights baked in.

**Open question.** Lane A may already build the worker API and scoring. Reconcile when their code lands: keep one implementation, prefer whichever is further along.

---

## Decisions

- ASR is CrisperWhisper, not WhisperX (user, 2026-11-02).
- Lane B owns Stage 0 passage preparation (user, 2026-11-02).
- Phonemes in the worker and BE contracts are IPA segments from espeak-ng conventions, after `ipa.py` normalisation.
- B0 (2026-11-02): shared types are frozen pydantic v2 models (`extra="forbid"`, tuples), not dataclasses. `Unit.passage_index` was added to the Rev 2 `Unit` (flag to the Lane A developer at reconciliation). The project uses a `src/audio/` layout and `requires-python = ">=3.11,<3.13"` (torch and transformers wheels lag new Python releases). A single `WordLabel` enum (contract values plus `correct`) is used everywhere; `STAGE3_LABELS` and `ARBITRATION_LABELS` are frozensets. `PreparedPassage.pronunciations` is stored as a read-only `MappingProxyType` and serialised as a dict.
- B1 (2026-11-02): espeak-ng via `phonemizer`, voice `en-us`, no stress marks; length marks are stripped (tense/lax survives in vowel quality). The lexicon replaces espeak (Filipino form first, anglicised second); `variants.yaml` adds to espeak's output. Heteronyms get both readings. The passage title is not read or scored. G2P input (`spoken_form`) and matching key (`interim_key`) are separate; `interim_key` is an interim stand-in for Lane A's Normalise (Rev 2 rules 1, 4, 7 only) and is the one function to replace at reconciliation. The lexicon lives in code-side YAML (`phonetics/data/`), not in `passages.proper_nouns`. `INVENTORY` is frozen in `ipa.py` (54 segments, including espeak's merged units such as `ɛɹ`, `ɑɹ`, `əl`, `aɪɚ`, and the Tagalog `a o u ɾ`). `LaneAResult` now rejects overlapping units. The six test passages were made-up placeholders (replaced in B1.1); the lexicon is unreviewed by a Filipino speaker.
- B1.1 (2026-11-02): the fixtures are the real passage texts (Supabase export, byte for byte; row 4 is the canonical "Every Saturday"; Anansi has no recording). `devtools/recordings.py` maps recording prefixes to passages. Lexicon lookup order is lexicon, then possessive of a lexicon word (each pronunciation plus a final `z`, and plus `s`; no `ɪz` after sibilants, a known ceiling), then espeak. Abbreviations and interjections (`mr`, `mrs`, `mmm`) live in the lexicon. A test fails when a capitalised mid-sentence word is in neither the lexicon nor the English-name allow-list. Spike re-run against the passage text is in `plans/phase-B2-spike.md` ("Passage reference"); the B2 model choice stands.
- B2 (2026-11-02): wav2vec2 CTC through `transformers`, greedy decoding, times on the 20 ms grid; 20 s chunks with 2 s context whose frames are discarded; WAV read with stdlib `wave` (no ffmpeg or torchaudio). `deadline_ms` is an absolute UTC epoch time in ms, as in the worker contract. espeak's merged units (`ɛɹ ɑɹ ɔɹ ɪɹ ʊɹ əl aɪə aɪɚ iə`) are now split into single segments by `SPLIT_MAP` plus a greedy fallback, and removed from `INVENTORY`; `g2p_golden.yaml` was regenerated once for this (9 words). `build_g2p(settings)` is the only place a G2P is built from settings (Windows default espeak path when unset). Model chosen by the pre-agreed rule: PER medians 0.358 (lv-60) and 0.368 (xlsr-53) are within 0.02, so the lower warm GPU latency wins, which is xlsr-53 by about 1% (noise-level; lv-60 has the slightly lower PER and fewer non-English phones). fp16 gives NaN/Inf logits on the GTX 1660 Ti, so it stays off; re-check on the A10G in the deployment benchmark. Non-English tokens are kept; the xlsr model emitted `e` (16 times) and a few `ø y ɨ x` on the 9 recordings, so B4 needs a feature distance for `e`. torch is >=2.6 from the cu126 index, because transformers refuses `torch.load` of the models' `pytorch_model.bin` below torch 2.6 (CVE-2025-32434) and torch 2.6+ has no cu121 wheels.
- B3 (2026-11-02): `lane_b/join.py` is a pure function returning `JoinResult` (`unit_phonemes`, `coverage_candidates`, `discarded`, `ms_per_phoneme`). Overlap rule: unpadded containment, nearest edge, nearer centre, lower index. `ms_per_phoneme` divides by the expected phoneme count (shortest variant), not the heard count, so a word hiding several attempts shows a long duration. Coverage runs break on silence over `AUDIO_COVERAGE_MAX_GAP_MS`; short runs are kept in `discarded` so the one-owner invariant is checkable. Timed omission units take part in the join; untimed units get no phonemes. The three settings are starting values; B6 calibrates them.
- B4 (2026-11-02): `lane_b/compare.py` has `compare`, `unit_variants` and `hypothesis_variants`; `Comparison` is in `types.py`. Costs and directional `[expected, heard]` accent pairs are data in `phonetics/data/accent_table.yaml`. Substitution: accent pair 0.1, else `0.4 + 0.6 × feature distance`; indel 1.0; deleting the last consonant of a final cluster 0.2. θ→t is low, not zero (SDD item for the team; B6 decides). Accent pairs and final-cluster drops are left out of `deviations`. Backtrace ties prefer diagonal, then deletion, then insertion. Multi-token units use the product of per-token variants, capped at 16. Hypothesis pronunciations come from a separate `PassagePreparer` with `cache_size=0`; a G2P failure disables `distance_hypothesis` for the request. Feature distances are a precomputed table (`feature_distances.json`), generated by `devtools/build_feature_distances.py` with panphon, which is not a dependency (it fails to import on Windows with the cp1252 default and pulls in pandas). Rerun the generator when the recogniser shows a symbol that is not in the table.
- B5 (2026-11-02): `arbitration/arbiter.py` is a pure `arbitrate(lane_a, passage, join, hypothesis, *, t_flag, t_overrule)` that builds the `WordVerdict`s itself (B4 `compare` and `unit_variants`); `join=None` is the degraded path (Stage 3 labels, `SKIPPED`, one warning). `phonetic_check` per word: `SKIPPED`, `UNVERIFIABLE` (untimed, or no heard phonemes on a non-omission, because that is more likely a recogniser gap than a misread), else `DONE`. A CORRECT word with `d_exp > T_OVERRULE` but no deviations stays CORRECT. SUB/MISPRON without a hypothesis distance keeps its Stage 3 label. Omissions are judged on their own joined phonemes (B3 puts the gap's phonemes on the omission unit, not in a coverage candidate); a hidden attempt in the gap is absorbed (look at it in B6). No abstention in v1; labels within ±0.05 of the threshold in use are logged (`near_threshold`). `AUDIO_T_FLAG` 0.25 and `AUDIO_T_OVERRULE` 0.45 are starting values, validated `0 <= T_FLAG < T_OVERRULE` at startup; B6's first check is short-word substitutions (cat→cap is about 0.17, under `T_FLAG`).
- Benchmark harness (`ce39c85`, 2026-11-02): arbitration gets an adaptive overrule for CORRECT words of 3 or fewer expected phonemes when heard > expected: the threshold becomes `T_OVERRULE + 1/n_expected`, to absorb one phoneme that bleeds in from the next word. `AUDIO_JOIN_PAD_MS` default lowered from 80 to 50. The recogniser loads `Wav2Vec2CTCTokenizer` + `AutoFeatureExtractor` directly, so no espeak backend is needed for it. The benchmark harness is `devtools/run_lane_b.py` and `devtools/transcribe_crisperwhisper.py`.
- I1 (2026-11-02): branch `feature/lane-b-integration` from `origin/dev` with `feature/lane-b` merged (no rebase); `feature/lane-b` is frozen. Lane B's `interim_key` and `tokenise` are gone: `PassagePreparer` takes one token per word of Lane A's `normalize`, so token positions equal Lane A's passage indices (`ice-cream` is two tokens, `5` is `five`); a test checks all 7 passages. One settings class: `config.AudioSettings` (uppercase field names, same `AUDIO_` env names), `settings.py` deleted, `AUDIO_PHONETIC_ENABLED` added. Lane A files touched, each small: `models.py` (`PhoneticEvidence`, optional row field `phonetic`, `ReviewContext`, `CONTRACT_VERSION` 5), `pipeline.py` (the `review` hook now takes a `ReviewContext` with the passage text, the proper-noun set and an optional `deadline_ms`; `run` takes `deadline_ms`), `tagging/mispronunciation.py` (skip rows with `phonetic.check == "done"`), `wiring.py`, `config.py`. `PhoneticReviewer` judges `match`, `substitution`, `mispronunciation`, `omission` and `insertion` rows and passes every other tag through; it changes only `tag` and `counted`; any exception or a passed deadline returns the rows unchanged (log `phonetic_check=skipped`). A name outside the lexicon is never turned from `match` into `mispronunciation` (`check` is `unverifiable`, Q7). The recogniser runs after ASR, sequentially: it is 2 to 7% of the request, so parallelising is not worth a Lane A change (Q5). Readiness: dev has no HTTP layer, so no `/health/ready` yet (I2). Step 6 found that at the default `JOIN_PAD_MS=50` the whisperx word windows are too tight: 88 of 112 label changes are false match-to-mispronunciation overrules on short words. B6 starts with the join window. Q1: torch 2.8.0+cu126 and whisperx 3.8.6 resolve together. Pre-existing: 34 pyright errors in `transcription/crisper_whisper.py` (same on `origin/dev` in this environment); Lane A's ruff is not clean either, so ruff runs on Lane B files only.
- B6a (2026-11-02): the CrisperWhisper word windows are tighter than the JOIN_PAD_MS=50 setting assumed, so the join is now **midpoint windows**: a unit's window is its span extended into each gap by half the gap and at most `AUDIO_JOIN_MAX_REACH_MS` (150); the first and last units reach 150 ms outward. The fixed pad (`JOIN_PAD_MS`, `JOIN_MODE`) is removed because midpoint measured the same (Q1). Arbitration gets three rules, all about overruling a Lane A `match`: a **coverage guard** (`AUDIO_COVERAGE_GUARD_RATIO` 1.0: Lane B must have heard at least that share of the word's shortest expected phonemes), a **short-word rule** (`AUDIO_SHORT_WORD_MAX_PHONES` 3: words with 3 or fewer expected phonemes are never overruled), and the existing adaptive overrule, which stays on (the sweep showed that turning it off doubles the false overrules, Q3). Blocked overrules keep Lane A's tag with `check=unverifiable` and `phonetic.reason` `low_coverage` or `short_word` (audio contract v6; `reason` is optional). An `ending` row (Lane A's `buys`/`buy`) can only end as `mispronunciation` or `match`, never `substitution`. The sweep is label-free: FOR (Lane A `match` rows changed / judged) and coverage, with a fixed split by passage (held-out: `marian_came` x2 and `nick_is_a_tri`), from a cache of the slow runs (`devtools/cache_runs.py`, `sweep_join.py`, `replay.py`; replay equals the live run on all 735 rows). Chosen: reach 150, guard 1.0, short-word 3, adaptive on: held-out FOR 1.91% (target 2%), coverage 93.4% (floor 85%), selection FOR 1.59%. I1's 15% became 1.6% to 1.9%. **`AUDIO_PHONETIC_ENABLED` now defaults to false** (the deployment ships Lane A only) until B6b shows against labels that Lane B improves on Lane A; measurement tools pass it on. Left for B6b: the remaining false overrules are mostly long words with a real divergence, `T_FLAG`/`T_OVERRULE`, near-equal vowels (cups/cops, mongo/mango, strict xfail in `tests/test_handchecked_i1.py`) and the `<=` at `T_FLAG` (jacky/jack).
- I2 (2026-11-03): branch `feature/audio-worker` from `feature/lane-b-integration` (origin/dev merged first). `audio/src/audio/worker/` implements the worker contract: one miscue per counted error (swap/moved halves and the rest of an insertion run merge into their counted row); `swap`, `moved` and `letter_transposition` map to `transposition`, `reversal` to `reversal`, `self_correction` is sent as `self-correction`; phonemes are Lane B evidence when present (heard only when `check == "done"`), otherwise espeak G2P, never with stress marks; `NO_SPEECH` only when ASR returns zero words, as a transcriber wrapper in the worker (`pipeline.py` untouched, Lane B stays on); the four flags are always `false` (logged once at startup). The image bakes in all models, including the phoneme model with Lane B off (`models.lock`, `devtools/fetch_models.py`), Python 3.12 (the project caps below 3.13). `compose.yaml`: only `audio-worker` changed (GPU reservation, `start_period` 180 s); `compose.fake-audio.yaml` restores the fake worker. Measured with `uvicorn` because the dev machine has no Docker: ready in 26 to 31 s, 17.6 to 19.3 s for a 44 s recording through the server, 4.6 GB VRAM with Lane B off. Open: the image is verified on EC2; repetition miscues have an empty `actual_phrase`; the four flags need team definitions. Lane A's ruff errors (206) and unformatted files are untouched.
