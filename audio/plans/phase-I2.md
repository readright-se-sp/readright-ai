# Phase I2: Audio worker HTTP API and image

Status: approved 2026-11-03. All open questions resolved: the recommendation stands for each.

## Why

Nothing in `audio/` serves [worker-api.md](../../contracts/worker-api.md). `compose.yaml` runs the server's fake worker (`devtools.fake_workers:audio_app`) as `audio-worker`, so the audio lane can't be deployed. This phase adds the worker app, the mapping from `AudioResult` to the worker response, the Docker image, and the compose wiring. It is the remaining part of the old B7 (see [phase-I1.md](phase-I1.md) "After I1").

Out of scope: the four audio flags (see Q6), B6b calibration, the A10G benchmark, any change to Lane A's tagging or Lane B's arbitration.

## Before starting

- Read `../CLAUDE.md`, `ROADMAP.md` ("How to work a phase"), `../contracts/worker-api.md`, the `Miscue`/`MiscueType` schemas in `../contracts/be-mai-openapi.yaml`, and `../video/app/{main,jobs,errors,models}.py` plus `../video/Dockerfile`. The video worker already implements this contract for its lane. Copy its structure (slot semaphore, disconnect polling, error handlers, readiness) into `audio/`. Don't import from it, because the projects share no code.
- Branch (Q1): `feature/audio-worker`, from `feature/lane-b-integration` after origin/dev has been merged into it.
- Docker data (Q8): check where Docker Desktop's disk image lives before the first build. If it's on C:, stop and ask.

## Files

New, in `src/audio/worker/`:

| File | Contents |
|---|---|
| `schemas.py` | Pydantic models matching worker-api.md: `AudioJob` (`job_id` UUID, `deadline` int, `wav_path` str, `passage_title` str, `passage_content` str), `AudioWorkerResult`, `WorkerMiscue`, `ErrorBody`/`ErrorResponse`, `ErrorCode` enum (`INVALID_JOB`, `MEDIA_UNREADABLE`, `NO_SPEECH`, `DEADLINE_EXCEEDED`, `INTERNAL`). Floats are finite, `wpm >= 0`, `0 <= reading_score <= 100`, `0 <= start_ms <= end_ms`. |
| `mapping.py` | `to_worker_result(job_id, result, passage_tokens, g2p) -> AudioWorkerResult`. Pure, no I/O. See "Mapping". |
| `jobs.py` | `JobRunner`: one `asyncio.Semaphore(WORKER_MAX_CONCURRENT_JOBS)`, `queue_length`, rolling `avg_processing_seconds` (last 20 jobs), deadline checks before and after waiting for the slot, disconnect polling while queued. Runs the pipeline in `asyncio.to_thread`. |
| `app.py` | `create_app(settings=None, pipeline_factory=None) -> FastAPI` (factory, used by uvicorn `--factory`). The lifespan loads the models in a thread (`load_transcriber`, `load_phoneme_recognizer`, `build_*`). `/health/ready` returns 503 until loading finishes and 200 `{"status": "ready"}` after. A failed load is logged and keeps returning 503; the process doesn't crash-loop. Error handlers make every non-2xx use the contract body. |
| `__init__.py` | Empty. |

Changed:

| File | Change |
|---|---|
| `src/audio/errors.py` | Add `NoSpeech`. |
| `src/audio/config.py` | Add the settings below. |
| `pyproject.toml`, `uv.lock` | Add `fastapi` and `uvicorn` (match the video project's pins), plus `httpx` (dev, for `TestClient`). |
| `Dockerfile` (new, `audio/`) | See "Image". |
| `devtools/fetch_models.py` + `models.lock` (new) | Download and convert all models at build time, with pinned Hugging Face revisions. |
| `../compose.yaml` | Change only the `audio-worker` service (Q7). |
| Docs | `audio/README.md` (run the worker locally, build the image), root `README.md` "Current state", `docs/deployment-architecture.md` (audio worker section, startup time), `ROADMAP.md` status row for I2. |

No contract changes: the response shape is worker-api.md 1.0.0 as written. If a mapping answer below needs a new field or enum value, stop and flag it.

## Settings (`AUDIO_` prefix)

| Setting | Default | Purpose |
|---|---|---|
| `WORKER_MAX_CONCURRENT_JOBS` | 1 | Jobs running on the GPU at once. The 1660 Ti has no room for 2. A10G tuning comes later. |
| `WORKER_ALLOWED_MEDIA_DIR` | `/scratch` | `wav_path` must resolve inside this directory, otherwise 400 `INVALID_JOB` (same rule as the video worker). |

Existing settings are unchanged. `PHONETIC_ENABLED` stays false, and the recogniser isn't loaded when it is false (`wiring.load_phoneme_recognizer` already does this).

## Request handling

Steps in order. Any exception not listed here becomes 500 `INTERNAL` and is logged with the traceback.

1. Body validation fails (missing field, bad UUID, extra types) → 400 `INVALID_JOB`.
2. `wav_path` is outside the allowed directory → 400 `INVALID_JOB`. `passage_content` has no words after Lane A's `tokenize_passage` → 400 `INVALID_JOB`.
3. `deadline <= now` → 504 `DEADLINE_EXCEEDED`.
4. Wait for the slot. If the client disconnects while waiting, give up quietly. If the deadline passes while waiting → 504.
5. Check the WAV before ASR with `recognition.audio_io.load_wav` (exists, 16 kHz / 16-bit / mono, has frames). Failure → 422 `MEDIA_UNREADABLE`. Running this first makes a bad file fail fast instead of going through whisperx's ffmpeg decode.
6. `pipeline.run(wav_path, passage_content, deadline_ms=deadline)` in a thread. ASR can't be interrupted. If the client disconnects mid-run, the thread finishes and the result is dropped (same as video).
7. ASR returned zero words → 422 `NO_SPEECH` (Q5). The check goes inside the worker, between transcribe and analyze. Either split `run` into its two calls in the worker, or add the check to `AudioPipeline.run` by raising `NoSpeech`. Prefer the worker-side split so Lane A's file stays untouched.
8. Deadline passed after the run → 504 `DEADLINE_EXCEEDED`.
9. Map (see below) and return 200, echoing `job_id`.

Logging: `job_id`, stage timings (ASR, review, total), error count, miscue count and `phonetic_check` state. Never the passage text or transcript. Paths are logged by filename only.

## Mapping (`AudioResult` → worker response)

**Scalars**
- `reading_score` = `score.word_recognition_pct`, clamped to 0–100.
- `wpm` = `score.wpm`, or `0.0` when it's `None`.
- `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous` and `is_hardly_audible` are all `false` (Q6). Log once at startup that they aren't implemented.

**Which rows become miscues: one miscue per counted error**, so the list agrees with `reading_score`.
- Every row with `counted == true` becomes one miscue.
- Rows that share a counted row's error are merged into it, not sent separately (Q3):
  - swap and moved halves with the same `move_id`
  - the uncounted rest of a contiguous insertion run (`post_pass` counts only the first)
- Merged miscues join the phrases in passage order (heard order for spoken), and the span runs from the earliest start to the latest end.
- `self_correction` rows (never counted) are sent or not depending on Q2b.
- `match`, `filler`, `false_start` and `off_text` are never sent.

**Tag → `miscue_type`** (Q2):

| Lane A tag | `miscue_type` |
|---|---|
| mispronunciation | mispronunciation |
| substitution | substitution |
| omission | omission |
| insertion | insertion |
| repetition | repetition |
| swap, moved | transposition |
| reversal | reversal |
| letter_transposition | **Q2a** |
| self_correction | **Q2b** |

Keep the table as one dict in `mapping.py`. A test fails if any value in `COUNTED_TAGS` has no entry.

**Phrases**
- `actual_phrase` = the raw passage tokens at `passage_indices`, joined with spaces, with punctuation stripped as `tokenize_passage` does. It is `""` for an insertion.
- `spoken_phrase` = `spoken_word`, and `""` for an omission.

**Phonemes** (Q4). IPA lists with no stress marks, using `phonetics.ipa` normalisation.
- `actual_phoneme`: `phonetic.expected_phonemes` when the row has Lane B evidence. Otherwise the espeak G2P best variant of `actual_phrase` (the G2P and passage preparer are built even when Lane B is off; they're CPU-only and cheap). `[]` for an insertion.
- `spoken_phoneme`: `phonetic.heard_phonemes` when Lane B checked the row (`check == "done"`). Otherwise G2P of `spoken_phrase`. `[]` for an omission.

**Times**, in integer ms, from seconds × 1000, rounded.
- Rows with both times use them.
- An omission, or any row with a missing time, spans the gap: `start_ms` = end of the previous timed row and `end_ms` = start of the next timed row. When the two touch or overlap, or only one side exists, the span has zero length at the side that exists. With neither side it is 0–0.
- Always clamp so that `0 <= start_ms <= end_ms`.

Miscues are ordered by `start_ms`, with passage order breaking ties.

## Image (`audio/Dockerfile`)

- **Base:** `nvidia/cuda:12.6.x-cudnn-runtime-ubuntu22.04` (cuDNN 9, which ctranslate2 4.x needs). Python 3.13 and uv come from `ghcr.io/astral-sh/uv`. The apt packages are `espeak-ng`, `ffmpeg` and `libsndfile1` if soundfile needs it. Torch comes from the cu126 index, as in `pyproject.toml`.
- **Build stage `models`:**
  - `devtools/fetch_models.py --model-dir /models` reads `models.lock`.
  - It converts `nyralabs/CrisperWhisper2.0_large` to CTranslate2 float16 using the command in `audio/README.md`, and fails the build if the `[verbatim_N]` tokens are missing.
  - It downloads the whisperx English alignment model and `facebook/wav2vec2-xlsr-53-espeak-cv-ft` (Q9).
  - The converter's heavy dependencies stay in this stage only.
- **Final stage:**
  - Copies `/models` and points `HF_HOME`, the torch hub cache and `AUDIO_ASR_MODEL` at it, with `HF_HUB_OFFLINE=1` so nothing downloads at runtime.
  - Runs as a non-root `mai` user and exposes 8001.
  - Healthcheck on `/health/ready` with `start-period=180s`.
  - `CMD uvicorn audio.worker.app:create_app --factory --host 0.0.0.0 --port 8001`.
- **Builds from a git checkout on EC2** (start-stack.sh, no registry), so the image can't use the git-ignored local `audio/models/`.

## `compose.yaml` (audio-worker only, Q7)

- `build: ./audio` and `image: readright-mai-audio`. Remove the fake-worker `command`.
- GPU: `deploy.resources.reservations.devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]`.
- `AUDIO_PHONETIC_ENABLED: ${AUDIO_PHONETIC_ENABLED:-false}`.
- Healthcheck `start_period: 180s`.
- Add a new `compose.fake-audio.yaml` override that puts back the server's fake worker, for machines without a GPU. Document it in the root README.

## Tests

Fast suite. No GPU and no models: a fake pipeline/transcriber is injected through `pipeline_factory`.

- **`tests/worker/test_mapping.py`:**
  - every tag row in the table, plus a test that every `COUNTED_TAGS` value has an entry
  - a swap gives 1 miscue, a moved pair gives 1, and an insertion run of 3 gives 1
  - miscue count equals `score.error_count` (plus self-corrections if Q2b is yes)
  - omission gap timing: between neighbours, touching neighbours, at the start, at the end, and no timed rows at all
  - multi-index rows (`cannot` for "can not")
  - phonemes with and without `phonetic`, and with `check != "done"`
  - stress marks are stripped
  - `wpm` of `None` gives `0.0`, and the score is clamped
- **`tests/worker/test_api.py`** (`TestClient`):
  - 200 echoes `job_id`
  - 400 for a bad UUID, a missing field, a path outside the allowed directory, and an empty passage
  - 422 `MEDIA_UNREADABLE` for a missing file, a 44.1 kHz stereo WAV and a zero-frame WAV
  - 422 `NO_SPEECH`
  - 504 for a deadline already passed on arrival, and for one that passes while queued (2 jobs, 1 slot)
  - 500 `INTERNAL` with the contract body when the pipeline raises
  - `/health/ready` gives 503 before load and 200 after, and 503 after a failed load
  - `/stats` gives `null` average before the first job, then a number and `queue_length`
- **`tests/worker/test_contract.py`:** the response JSON keys equal the keys in worker-api.md's audio example, and the `miscue_type` values are a subset of the BE `MiscueType` enum (parsed from the YAML).
- **Slow, marked `gpu`** (skipped without CUDA or the CT2 model): the real app on one recording through `TestClient`. It must return 200 and a valid body. Record the wall time.

## Done when

- `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and pyright are clean on the new files. The ruff E501 errors in `devtools/benchmark.py` are fixed too.
- On the dev machine:
  - `docker compose up` with the real audio worker becomes ready.
  - One real recording through the server's `POST /v1/analyses` (moto S3) returns 200 with miscues, clips and phonemes.
  - Record the image size, time to ready and request time in the Results section.
  - If Docker can't use the GPU on this machine, record that, and run the worker with `uv run uvicorn ...` against the server instead. The image is then verified on EC2 by the deploying teammate.
- Docs are updated, and I2 is marked done in `ROADMAP.md` and the root README.
- Commits use Conventional Commits with no Claude or Co-Authored-By trailers. Don't push unless asked.

## Open questions (all resolved 2026-11-03: the recommendation stands for each)

- **Q1. Branch.** Recommend `feature/audio-worker` from `feature/lane-b-integration` after merging origin/dev. The I1 + B6a PR can then be reviewed on its own, and the I2 PR stacks on it. Alternative: keep working on `feature/lane-b-integration`. That makes one bigger PR and holds up the review.
- **Q2a. `letter_transposition`.** The interim mapping given to the team says `reversal`. But Lane A's own definition (`models.py`, `tagging/reversal.py`) says Phil-IRI transposition = swap + moved + letter_transposition, with reversal kept for words read backwards (was/saw). **Recommend `transposition`**, which follows Lane A's definition, and correcting the note to the team. Confirm with the Lane A developer.
- **Q2b. `self_correction`.** The BE `MiscueType` enum has 8 values and includes `self-correction`; the interim note said 7 and dropped it. **Recommend sending it** as `self-correction`. It is outside `reading_score` (never counted), and the BE gets the clip. Alternative: drop it until the team decides.
- **Q3. One miscue per error.** Recommend merging swap/moved halves and insertion runs into the counted row, so the miscue count equals the error count. Alternative: one miscue per row, where a swap gives 2 transposition entries and the BE would show more errors than the score counts.
- **Q4. Phonemes while Lane B is off** (the shipping default). Recommend espeak G2P of `actual_phrase` and `spoken_phrase`. That gives dictionary pronunciations, so a mispronunciation that CrisperWhisper normalised shows identical lists. Alternative: `[]` when there is no Lane B evidence. That's valid against the schema but tells the BE nothing. G2P is cheap, and espeak is in the image anyway.
- **Q5. `NO_SPEECH`.** Recommend raising it only when ASR returns zero words. A recording of only fillers or off-text talk still returns 200 with all omissions, because the teacher should see that.
- **Q6. The four flags** (`is_word_by_word`, `is_disregard_punctuation`, `is_monotonous`, `is_hardly_audible`). Recommend `false`, with a startup log line and a README note that they aren't implemented. Each needs a definition from the team before it is built, so it goes in a later phase. Tell the deploying teammate the BE will always see `false` for now.
- **Q7. compose.yaml.** It's a shared root file, owned with the server side. Recommend changing only the `audio-worker` service and adding `compose.fake-audio.yaml` for GPU-less machines. Tell the teammate in the PR description.
- **Q8. Docker on the dev machine.** The image is roughly 10–12 GB (CUDA, torch, about 3 GB of models). Recommend checking that Docker Desktop's WSL disk image is on D: before any build. If it isn't, move it in Docker Desktop's settings (Resources → Disk image location) or skip the local image build and verify on EC2.
- **Q9. Bake the phoneme model while Lane B is off.** Recommend yes (about 1.2 GB), so turning Lane B on is an env change with no rebuild, as already promised to the team.
- **Q10. Code review.** `worker/` is new code, but the mapping encodes Lane A's tag meanings. Recommend asking the Lane A developer to review `mapping.py` and the tag table in the I2 PR.
