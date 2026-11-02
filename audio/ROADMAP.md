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
| B1 | Stage 0 passage preparation (espeak-ng IPA) | Not started |
| B2 | Phoneme recogniser spike and implementation | Not started |
| B3 | Join by time and coverage candidates | Not started |
| B4 | Compare (weighted phoneme distance, accent table) | Not started |
| B5 | Arbitration | Not started |
| B6 | Calibration on real recordings | Not started |
| B7 | Worker API, scoring and contract mapping (interim) | Not started |

## How to work a phase

1. **Before planning**, read: this file, [../CLAUDE.md](../CLAUDE.md), [the worker contract](../contracts/worker-api.md), [the BE contract](../contracts/be-mai-openapi.yaml) (miscue shape), [the deployment doc](../docs/deployment-architecture.md) (GPU, time budget), and the current code in `src/` and `tests/`.
2. **Draft a plan** in `plans/phase-<id>.md` (planning and implementation run in separate sessions, so the plan file is the hand-off): files to add or change, new settings, new dependencies, behavior, tests, done criteria, and the phase's open questions with a recommended answer for each. Do not resolve an open question silently.
3. **Wait** for the user's approval of the plan and an explicit go. Never start a phase, or the next one, unprompted.
4. **Implement**, then run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
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

## Phase B3 — Join by time

**Build.**
- Each `PhonemeToken` goes to the unit whose window `[start_ms − JOIN_PAD_MS, end_ms + JOIN_PAD_MS]` contains its midpoint. Overlapping windows go to the unit with the nearer centre.
- Phonemes outside every window are grouped into runs. A run of at least `COVERAGE_MIN_MS` becomes a `CoverageCandidate`, tagged with the passage position it follows. These feed the post-pass and the OMISSION rule in arbitration.
- Units with `verifiable=False` (no timing) get no phonemes.
- Duration ratio per unit: `(end_ms − start_ms) / max(1, len(heard_phonemes))`, handed to the post-pass.

**Settings.** `AUDIO_JOIN_PAD_MS` (start at 80, try 50–100), `AUDIO_COVERAGE_MIN_MS` (start at 120, measure).

**Tests.** Boundary phonemes, overlapping windows, gaps that do and do not reach `COVERAGE_MIN_MS`, units without timing.

## Phase B4 — Compare

**Build.**
- Weighted Levenshtein over phoneme lists, with a backtrace.
  - Substitution cost: about 0.1 for accent-table pairs; otherwise the articulatory-feature distance from `panphon`, scaled to 0–1.
  - Insertion and deletion cost: 1.0.
  - Final-cluster simplification: deleting the last consonant of a word-final cluster costs about 0.2.
- `distance_expected = min over variants of cost / len(variant)`.
- `distance_hypothesis`: the same comparison against the IPA of CrisperWhisper's word (G2P on the fly, cached). `None` when the word matched.
- `deviations`: substitution, insertion and deletion pairs from the best backtrace, with accent-table pairs left out.
- Accent table (spec §6): f→p, v→b, θ→t, ð→d, z→s, ʒ→ʃ, ɪ↔i, ʊ↔u, æ→a/ɛ, full vowel→ə. It is data in one file, so the weights can be calibrated in B6.

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
| OMISSION | no speech phonemes in the gap | OMISSION |
| OMISSION | coverage candidate in the gap close to the passage word (`≤ T_FLAG`) | CORRECT |
| any | `verifiable=False` or phonetic check skipped | Stage 3 label |

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
