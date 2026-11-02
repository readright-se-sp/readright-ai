# Phase I1: Integrate Lane B into Lane A (on `dev`)

Status: approved (user, 2026-11-02), with the recommended answer for every open question below (Q1–Q7; Q7 approved separately). Ready for implementation in a separate session, starting at Step 0.

## Why this phase, and what it replaces

Lane A has been merged into `origin/dev` (PR #23, `fix/audio-retry-next-word`), and so has the video lane. Lane A brought its own `pipeline.py`, `wiring.py`, `PostPass`, `ReadingScorer`, a 14-tag row model (`models.py`) and its own contract `contracts/audio-service.md` (v4). It also left a hook for Lane B: `RowReviewerProtocol.review(wav_path, rows) -> rows`, run in `AudioPipeline._analyze` between `WordLane.rows` and `PostPass.run`.

Consequences (user decisions, 2026-11-02):
- **B7 is superseded.** Lane A already has the pipeline, post-pass and scoring. Anything B7 still needs (phoneme fields in the response, the worker API if missing) is done here or in a later phase on top of Lane A's code.
- **B6 moves after I1.** Calibration needs real Stage 3 labels from Lane A, not the B0 fixtures.
- **Branch:** a new `feature/lane-b-integration` created from `origin/dev`, with `origin/feature/lane-b` merged in (merge, no rebase: lane-b is already pushed). `feature/lane-b` is then frozen. This replaces the roadmap's single-branch rule for this phase and the ones after it.
- **Owner:** the Lane B developer does the integration. The Lane A developer reviews the PR into `dev`. Treat Lane A code as fixed except at the hook points listed below, and keep each change to Lane A files small and separate so it is easy to review.
- **Mispronunciation:** arbitration replaces Lane A's letter-based `MispronunciationTagger` on rows that Lane B judged. The tagger stays as the fallback for rows Lane B skipped, and for the whole request when Lane B fails or runs out of time.

## Before starting

Read these: `../CLAUDE.md`, this roadmap, `contracts/audio-service.md` (on dev), `contracts/worker-api.md`, and on dev `audio/src/audio/{pipeline,wiring,models,config}.py`, `audio/src/audio/tagging/{post_pass,mispronunciation,attempts}.py`, `audio/src/audio/alignment/text.py` (`normalize`, `tokenize_passage`), `audio/OPEN_QUESTIONS.md`.

## Step 0: housekeeping on `feature/lane-b` (one commit, then push)

- ROADMAP Decisions: add an entry for `ce39c85`:
  - Adaptive overrule for CORRECT words of 3 or fewer expected phonemes when heard > expected: `T_OVERRULE + 1/n_expected`. Its purpose is to absorb one phoneme that bleeds in from the next word.
  - `AUDIO_JOIN_PAD_MS` default lowered from 80 to 50.
  - The recogniser loads `Wav2Vec2CTCTokenizer` + `AutoFeatureExtractor` directly, so no espeak backend is needed.
  - The benchmark harness is `devtools/run_lane_b.py` and `devtools/transcribe_crisperwhisper.py`.
- ROADMAP status table: B7 becomes "Superseded by Lane A + I1". Add an I1 row. B6 becomes "After I1".
- Commit this plan file with it.

## Step 1: branch and merge

```
git switch -c feature/lane-b-integration origin/dev
git merge --no-ff origin/feature/lane-b
```

Expected conflicts and how to resolve them:

| File | Resolution |
|---|---|
| `audio/pyproject.toml` | One project. Keep Lane A's `name`, its `whisperx` dependency and pyright config. Add Lane B's runtime dependencies (`transformers`, `torch` from the cu126 index, `phonemizer`, `pyyaml`, …), its `requires-python = ">=3.11,<3.13"`, and ruff config. Switch the build to uv (`uv.lock` comes from lane-b). Re-lock with `uv lock`. See open question Q1 (torch versions). |
| `audio/src/audio/__init__.py` | Union of both. |
| `audio/tests/conftest.py` | Union of both fixtures, and keep Lane B's slow/GPU markers. |
| Root `README.md`, `CLAUDE.md`, `docs/deployment-architecture.md` | Take dev's version, then re-apply Lane B's additions (lane-b-context pointer, GPU notes). |

Done when: both test suites pass unchanged on the merged tree (`uv run pytest`), along with `ruff check`, `ruff format --check` on Lane B files and `pyright` on Lane A files (as each project configured them). No behavior change yet. Commit: `chore(audio): merge lane B into dev`.

## Step 2: one normaliser and one settings class

- **Normaliser.** Replace Lane B's `interim_key` (B1) with Lane A's `alignment.text.normalize` / `tokenize_passage`, so passage positions match exactly. `PreparedPassage` must be indexed by Lane A's passage index. Lane B's `spoken_form` (G2P input) stays. Delete `interim_key` and the tests for it. Add a test that `prepare(passage)` and `tokenize_passage(passage)` give the same length and order for all 7 real passages.
- **Settings.** Merge into one class. Recommendation (Q2): Lane A's `config.AudioSettings` gets Lane B's fields (`phoneme_*`, `join_pad_ms`, `coverage_*`, `t_flag`, `t_overrule`, espeak path), the `T_FLAG < T_OVERRULE` validator moves with them, and `settings.py` is deleted. Keep the `AUDIO_` env names Lane B already documented.
- **Types.** Lane B's pydantic `types.py` stays internal to Lane B. Rows crossing the hook are Lane A's `AlignedWord`. No change to `models.py` in this step.

## Step 3: the Lane B reviewer (new `audio/src/audio/lane_b/reviewer.py`)

`PhoneticReviewer` implements `RowReviewerProtocol`. Constructor dependencies: recogniser, passage preparer, hypothesis preparer, thresholds, logger.

`review(wav_path, rows)`:
1. Adapter rows → `LaneAResult`:
   - Times are seconds → ms (`round(s * 1000)`).
   - `passage_indices` → `Unit.passage_span` / `passage_index`.
   - `spoken_word` → surface.
   - `start/end is None` → `verifiable=False`.
   - Tag mapping to `WordLabel`: `match`→CORRECT, `substitution`→SUBSTITUTION, `mispronunciation`→MISPRONUNCIATION, `omission`→OMISSION, `insertion`→INSERTION.
   - Every other tag (repetition, swap, moved, reversal, filler, false_start, off_text, self_correction, letter_transposition) is **not judged**: pass it through untouched.
   - Q3 is whether swap/moved rows should get phonetic evidence later. Recommendation: no, in v1.
2. Run the recogniser on `wav_path` (B2), then `join` (B3), then `arbitrate` (B5).
3. Write results back onto the rows. Change only `tag` (and `counted` if the tag's counted-ness changes, using Lane A's `retag` helper). Never add or remove rows (the hook's contract: same order, same coverage).
4. Mark judged rows so the post-pass can tell them apart (Q4). Recommendation: a new optional row field `phonetic: PhoneticEvidence | None` with `expected_phonemes`, `heard_phonemes`, `d_exp`, `d_hyp`, `check` (`done|unverifiable|skipped`). It changes the response shape, so it needs a contract bump (Step 5).
5. Errors and the deadline:
   - Any exception, or the deadline passing, returns `rows` unchanged.
   - Log `phonetic_check=skipped` with the reason.
   - The request never fails because of Lane B.

Note that the recogniser now runs **after** ASR, not in parallel as the roadmap's diagram shows. Keep it sequential in I1 and measure it (Q5).

## Step 4: post-pass changes (Lane A files, small)

`MispronunciationTagger.tag` skips rows where `row["phonetic"]` exists and `check == "done"`. Everything else is unchanged. The attempts and reversal taggers and the insertion runs are untouched. Tests:
- A Lane-B-judged substitution stays a substitution even when the spelling is close.
- A row Lane B skipped still gets the letter-based tag.
- With `reviewers=()`, the output is byte-identical to before (Lane A's `test_samples.py` passes unchanged).

## Step 5: contract and wiring

- `contracts/audio-service.md`: document the optional `phonetic` object per row (IPA segments after `ipa.py` normalisation, distances, check). Bump `CONTRACT_VERSION` 4 → 5 in `models.py`. The mapping to the BE's `actual_phoneme`/`spoken_phoneme` is done in I2, not here (see Q6).
- `wiring.py`:
  - `load_phoneme_recognizer(settings)` is loaded once at startup, next to `load_transcriber`.
  - `build_audio_pipeline` takes an optional reviewer.
  - `AUDIO_PHONETIC_ENABLED` (default true) lets Lane B be turned off without a deploy.
- Readiness: if the audio service has a `/health/ready`, it reports not-ready until both models are loaded. If no HTTP layer exists yet on dev, note it and skip.

## Step 6: end-to-end check (no tuning)

- Extend `devtools/run_lane_b.py` (or add `devtools/run_pipeline.py`) to run the full `AudioPipeline` on the 9 recordings in `audio/data/`. Output per-row Lane A tag vs final tag, `d_exp`, `d_hyp`, heard IPA, and timing for ASR, recogniser and total.
- Record in this file:
  - how many rows Lane B changed per type,
  - 5–10 hand-checked changes (right or wrong),
  - wall-clock and peak VRAM with both models loaded.
- This is the input to B6, not calibration. Thresholds stay at the current defaults.

## Tests (summary)

- Adapter: tag mapping, seconds↔ms, untimed rows, multi-index rows, pass-through tags.
- Reviewer: row count and order kept; recogniser exception → rows unchanged and logged; `counted` is consistent after a retag.
- Post-pass skip rule (Step 4).
- Normaliser parity on the 7 passages (Step 2).
- One slow (GPU-marked) test: full pipeline on one fixture WAV.

## Done when

- `feature/lane-b-integration` passes `uv run pytest` (slow tests skippable), `ruff`, and `pyright` on Lane A files.
- Step 6 numbers are recorded.
- ROADMAP, root README "Current state", and `docs/deployment-architecture.md` (the second GPU model, time budget) are updated.
- The PR to `dev` is opened only when the user says so. Then the Lane A developer reviews it.

## Open questions (all resolved 2026-11-02: the recommendation stands for each)

- **Q1 torch/whisperx versions.** whisperx pins its own torch. Lane B needs torch ≥ 2.6 (cu126) for the `torch.load` CVE gate. Recommendation: try the cu126 index for both. If whisperx refuses, stop and report the conflict. Don't pin around it silently.
- **Q2 settings.** Merge into Lane A's `AudioSettings` (above). Alternative: two settings classes, which is simpler to merge but leaves two config sources.
- **Q3 swap/moved/repetition rows.** Not judged in v1.
- **Q4 how judged rows are marked.** An optional `phonetic` field (contract bump to v5). Alternative: a private side-table returned with the rows. No contract change, but the evidence then can't reach the BE.
- **Q5 sequential vs parallel recogniser.** Sequential in I1. If Step 6 shows ASR + recogniser takes more than ~25% of the 115 s budget on a 2-minute reading, a follow-up starts the recogniser in a thread inside `AudioPipeline.run`. That is a Lane A file change, so agree it with the Lane A developer.
- **Q6 HTTP layer (revised 2026-11-02).** `contracts/worker-api.md` is the server-to-audio interface: the server calls `POST /v1/jobs` on `audio-worker:8001` and expects BE-shaped miscues back. `audio-service.md` is Lane A's internal result format, and its request section is marked "Not final". Nothing in `audio/` implements `worker-api.md` yet. The HTTP layer is out of scope for I1 and becomes phase I2. In Step 5, the phoneme fields therefore go on the `audio-service.md` rows only. The mapping to `actual_phoneme`/`spoken_phoneme` happens in I2.

- **Q7 proper nouns (added 2026-11-02, after reviewing `fix/audio-retry-next-word`).** Lane A forgives a same-sounding spelling for names (`is_proper_noun`, `sound_key`). The request's `proper_nouns`, or `guess_proper_nouns` when none are given, decide which words are names. Lane B's espeak G2P is unreliable for Filipino names that aren't in `lexicon.yaml`. Recommendation:
  - For a name that **is** in the lexicon, arbitrate normally.
  - For a name that is **not** in the lexicon, never overrule CORRECT → MISPRONUNCIATION; log it instead (`phonetic_check=unverifiable`, reason `name_not_in_lexicon`).
  - Pass the request's name set into the reviewer for this check.

## Notes from `fix/audio-retry-next-word` (already in `dev`, no separate merge needed)

Both commits (`d71c1a9`, `bf98af3`) are in `origin/dev` through PR #23, so Step 1 picks them up automatically. Points that affect this phase:
- **ASR-joined and split words** (`d71c1a9`: `cut_like` and the aligner changes). One heard word can cover several passage words ("cannot" for "can not"), and one passage word can be heard as several pieces. The adapter must handle rows with several `passage_indices` and rows whose times are shared or `None` after a cut. Add an adapter test for each case, using the examples in Lane A's `test_tagging.py`.
- **Normaliser parity** (Step 2). `text.py` gained `sound_key`, `is_proper_noun` and `guess_proper_nouns`. Use Lane A's `tokenize_passage`/`normalize` as the only source of passage positions. Lane B's test that capitalised mid-sentence words must be in the lexicon should check against `guess_proper_nouns`, not its own rule.
- **Retry of the next word** (`bf98af3`). It produces repetition and self-correction rows. These are pass-through tags for the reviewer, so nothing changes here. Add one such row to the pass-through test.

## After I1

- **I2, the audio worker API** (the remaining part of B7). A FastAPI app in `audio/` that implements `worker-api.md`: `POST /v1/jobs`, `GET /health/ready`, `GET /stats`, deadline handling (`DEADLINE_EXCEEDED`), `MEDIA_UNREADABLE`, `NO_SPEECH`. It maps `AudioResult` to the worker response: `reading_score`, `wpm`, the four flags, and miscues with `actual_phoneme`/`spoken_phoneme` from the `phonetic` field. It runs `AudioPipeline` through `asyncio.to_thread`. It also needs a Dockerfile (CUDA, espeak-ng, both models). Owner: unknown, so confirm with the Lane A developer. Recommendation: the Lane B developer builds it, because B7 was already theirs.
  - **Tag-mapping gap to resolve with the team.** The BE `miscue_type` enum has 7 values (mispronunciation, omission, substitution, insertion, repetition, transposition, reversal). Lane A produces 14 tags. Unclear mappings: `swap`/`moved` → `transposition`? `letter_transposition` → `reversal`? Are `self_correction`, `filler`, `false_start` and `off_text` dropped, or returned uncounted? Do not invent BE enum values. If a tag has no home, flag it as a contract change.
- **B6, calibration** on the integrated pipeline. `samples.json` on dev is made-up text with expected counts, not labels for the 9 real recordings. Unless the team turns out to have teacher labels, B6 starts by labelling the recordings: a CSV of `recording, passage_index, passage_word, label`, with a held-out split. B6 can run before or after I2.
