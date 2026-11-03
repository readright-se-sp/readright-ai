# audio

The audio lane of the ReadRight MAI. It implements the GO2 pipeline behind the worker contract in [../contracts/worker-api.md](../contracts/worker-api.md). Lane A (word lane, post-pass) and Lane B (phonetic lane, arbitration) are built by different developers; see [ROADMAP.md](ROADMAP.md).

Current state: Lane A (CrisperWhisper word lane, post-pass, scoring) and Lane B (phoneme recogniser, join, compare, arbitration) are integrated in one pipeline (Phase I1): `AudioPipeline` in `src/audio/pipeline.py`, composed in `src/audio/wiring.py`. Lane B plugs in as `PhoneticReviewer` (`src/audio/lane_b/reviewer.py`) and adds an optional `phonetic` object to each row it judged (contract v7, [../contracts/audio-service.md](../contracts/audio-service.md)). **`AUDIO_PHONETIC_ENABLED` defaults to true** (set it to false for Lane A alone; then no phoneme model is loaded). Phase B6b showed against 735 hand labels that Lane B improves on Lane A as a repair step ([plans/phase-B6b.md](plans/phase-B6b.md)): `AUDIO_OVERRULE_MATCH` is false (Lane B never turns a `match` into an error), `AUDIO_T_FLAG` 0.25 with `<` (`AUDIO_T_FLAG_INCLUSIVE=false`). Phase B6a cut the false overrules of correct words from 15% to 1.6 to 1.9% on the 9 recordings ([plans/phase-B6a.md](plans/phase-B6a.md)): midpoint join windows (`AUDIO_JOIN_MAX_REACH_MS` 150), a coverage guard (`AUDIO_COVERAGE_GUARD_RATIO` 1.0) and a short-word rule (`AUDIO_SHORT_WORD_MAX_PHONES` 3). `devtools/score_labels.py` scores any setting against the labels from the cache. The worker HTTP API and the Docker image are done (Phase I2, `src/audio/worker/`, `Dockerfile`; see "Run the worker" and "Build the image" below).

## Layout

```
audio/
├── src/audio/        # package: worker/ (HTTP API, I2); flags/ (the four audio flags); pipeline.py, wiring.py, models.py, config.py (settings), alignment/, tagging/, transcription/ (Lane A); types.py, passage.py, phonetics/, recognition/, lane_b/, arbitration/ (Lane B)
├── models/           # converted CrisperWhisper (git-ignored)
├── devtools/         # run_pipeline.py (full pipeline on data/), run_lane_b.py, phoneme_spike.py, flag_report.py (output in devtools/out/, git-ignored)
├── tests/            # pytest; fixtures in tests/fixtures/
├── data/             # local recordings (git-ignored, never commit)
└── pyproject.toml
```

## Install and test

Needs Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/). From `audio/`:

```
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

`ffmpeg` must be on `PATH` (whisperx shells out to it to read WAVs): Windows `winget install Gyan.FFmpeg` (open a new terminal afterwards), Linux/Docker `apt-get install ffmpeg`. Check with `ffmpeg -version`.

Settings use the `AUDIO_` prefix; copy `.env.example` to `.env` to override them.

Tests marked `model` need the model weights (downloaded on first use) and tests marked `gpu` need CUDA; each is skipped with a reason when its requirement is missing.

### PyTorch and the phoneme model

- `torch` comes from the CUDA 12.6 wheel index (`pytorch-cu126` in `pyproject.toml`) on Windows and Linux; the wheels bundle their own CUDA runtime and need a driver from the 12.x series (528.33 or newer on Windows). torch 2.6 or newer is required, because `transformers` refuses to load the models' `pytorch_model.bin` on older versions. The wheel is about 2.5 GB, so check free disk space; set `UV_CACHE_DIR` to another drive if the default one is small.
- Model weights download on first use to the Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface`). Each candidate is about 1.3 GB. Set `HF_HOME` to relocate it. The Docker image bakes the weights in (`devtools/fetch_models.py`, see "Build the image").
- The default model is `facebook/wav2vec2-xlsr-53-espeak-cv-ft` in fp32 (`AUDIO_PHONEME_MODEL`, `AUDIO_PHONEME_FP16`); see [plans/phase-B2-spike.md](plans/phase-B2-spike.md).
- Spike: put the recordings in `data/`, then `uv run python devtools/phoneme_spike.py` (downloads Whisper small.en and both models, about 5 GB; takes about 15 minutes on a GTX 1660 Ti). Use `--summary-only` to rebuild `plans/phase-B2-spike.md` from `devtools/out/results.csv`.

### ASR model (CrisperWhisper 2.0 large, CTranslate2)

Lane A loads `AUDIO_ASR_MODEL` (default `models/crisper2_large_ct2`, relative to `audio/`) through whisperx on CUDA in float16. The folder is git-ignored (about 3 GB for float16) and is made once with the CTranslate2 converter (from `audio/`, after `uv sync --extra dev`):

```
uv run ct2-transformers-converter --model nyralabs/CrisperWhisper2.0_large --output_dir models/crisper2_large_ct2 --copy_files tokenizer.json preprocessor_config.json --quantization float16
```

`load()` raises "not a CrisperWhisper 2.0 model" if the `[verbatim_N]` tokens are missing. On the 6 GB GTX 1660 Ti, ASR (3.3 GB) plus the phoneme model fills the card (6.1 GB peak); `AUDIO_ASR_COMPUTE_TYPE=int8_float16` is the fallback if it runs out of memory. `uv run python devtools/run_pipeline.py` runs everything on the recordings in `data/`. Tests marked `gpu` skip when CUDA or this folder is missing.

### espeak-ng (system dependency)

Passage phonemes come from [espeak-ng](https://github.com/espeak-ng/espeak-ng), which `phonemizer` loads as a system library.

- Windows: install the `.msi` from the espeak-ng GitHub releases (or `winget install eSpeak-NG.eSpeak-NG`), then set `AUDIO_ESPEAK_LIBRARY=C:\Program Files\eSpeak NG\libespeak-ng.dll` in `audio/.env`. It is exported as `PHONEMIZER_ESPEAK_LIBRARY` before the backend is created.
- Linux/Docker: `apt-get install espeak-ng`.

On Windows, `build_g2p` uses `C:\Program Files\eSpeak NG\libespeak-ng.dll` when `AUDIO_ESPEAK_LIBRARY` is unset and the file exists, so no `.env` is needed on a default install. Tests marked `espeak` are skipped, with a reason, when the library cannot be loaded. Other settings: `AUDIO_ESPEAK_LANGUAGE` (default `en-us`) and `AUDIO_PASSAGE_CACHE_SIZE` (default `128`).

## Run the worker

The worker serves [../contracts/worker-api.md](../contracts/worker-api.md): `POST /v1/jobs`, `GET /health/ready` (503 until the models are loaded, then 200) and `GET /stats`. From `audio/`, with the ASR model in `models/crisper2_large_ct2` (or `AUDIO_ASR_MODEL` pointing at it):

```
AUDIO_WORKER_ALLOWED_MEDIA_DIR=<folder the WAVs are in> uv run uvicorn audio.worker.app:create_app --factory --port 8001
```

Startup takes about 30 s on the dev GPU (ASR and alignment; the phoneme model only loads when `AUDIO_PHONETIC_ENABLED=true`). `wav_path` must be an absolute path inside `AUDIO_WORKER_ALLOWED_MEDIA_DIR` (default `/scratch`, the shared volume in compose), otherwise the job is rejected with 400 `INVALID_JOB`. To run the whole stack on this machine, point the server at it: `MAI_AUDIO_WORKER_URL=http://localhost:8001`, with the server's scratch directory as the allowed media directory.

| Setting | Default | Purpose |
|---|---|---|
| `AUDIO_WORKER_MAX_CONCURRENT_JOBS` | 1 | Jobs on the GPU at once. The 6 GB dev card has no room for 2. |
| `AUDIO_WORKER_ALLOWED_MEDIA_DIR` | `/scratch` | `wav_path` must resolve inside it. |
| `AUDIO_WORKER_STATS_WINDOW` | 20 | Jobs in the rolling average of `/stats`. |
| `AUDIO_WORKER_DISCONNECT_POLL_S` | 0.5 | How often a waiting or running job checks that the server is still connected. |
| `AUDIO_WORKER_LOG_LEVEL` | `INFO` | JSON logs with the `job_id`; never passage text, transcripts or full paths. |

How a job is answered: 400 `INVALID_JOB` (bad body, path outside the directory, passage with no words), 504 `DEADLINE_EXCEEDED` (already passed, passed while queued, or passed during the run), 422 `MEDIA_UNREADABLE` (WAV missing, not 16 kHz / 16-bit / mono, or empty), 422 `NO_SPEECH` (ASR returned zero words), 500 `INTERNAL` for anything else. A failed model load is logged and `/health/ready` stays 503; the process does not exit.

The response has one miscue per counted error (`score.error_count`), mapped in `worker/mapping.py`: `swap`, `moved` and `letter_transposition` are `transposition`; `reversal` is `reversal`; `self_correction` is `self-correction` (sent although it is not counted); `match`, `filler`, `false_start` and `off_text` are not sent. The two halves of a move and the rest of an insertion run are merged into their counted row. Phonemes come from Lane B when it judged the row (heard phonemes only when `check == "done"`), otherwise from espeak G2P of the phrase, without stress marks. Times are in ms; an omission spans the gap between its neighbours. A repetition has an empty `actual_phrase` (Lane A gives it no passage index).

The four flags `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous` and `is_hardly_audible` are computed by the worker after the pipeline runs; see "Audio flags" below. `AUDIO_FLAGS_ENABLED=false` returns all four as `false`.

Tests: `tests/worker/` (fast, fake pipeline). `tests/worker/test_worker_slow.py` runs the real app on one recording and needs the GPU, the ASR model and a `ring*.wav` in `data/`.

## Audio flags

The worker returns four yes/no flags for the whole reading, like the boxes a teacher ticks on the Phil-IRI observation checklist. They are computed in `src/audio/flags/` from Lane A's word rows (who said what, when) and the recording. They do not change the score or the miscue list.

When there isn't enough evidence (too few words with times, too few full stops), the flag is `false` and the worker logs `insufficient` in the flag metrics. A false "yes" is worse for a child than a missed one. If a flag fails for any reason, that flag is `false` and the request still succeeds.

**These are working definitions. The team has not yet confirmed them, and every threshold is a setting (`AUDIO_` prefix) that can change.**

| Flag | It is `true` when | Default thresholds | Limits |
|---|---|---|---|
| `is_word_by_word` | at least half of the pauses between neighbouring words inside a phrase are 250 ms or longer | `WBW_GAP_MS` 250, `WBW_MIN_SHARE` 0.5, at least 10 gaps | Word times come from the speech model and are rounded to 20 ms. Pairs next to a skipped word, an extra word, a filler or a repetition are not counted. |
| `is_disregard_punctuation` | fewer than half of the full stops (`. ! ? ;`) are followed by a real pause: at least 300 ms and at least 1.5 times the reader's usual gap between words | `PUNCT_SENTENCE_PAUSE_MS` 300, `PUNCT_REL` 1.5, `PUNCT_MIN_SHARE` 0.5, at least 3 full stops | Commas are measured (in the logged metrics) but do not decide the flag. A full stop that ends an abbreviation counts as a sentence end. |
| `is_monotonous` | the pitch of the reader's words varies little: the spread of the per-word pitch is under 2 semitones | `MONO_MAX_ST` 2.0, pitch range 75 to 600 Hz, at least 15 voiced words | Pitch comes from Praat. A whispered or noisy recording has few voiced words and gives `false`. Words about an octave from the median are treated as tracker errors and dropped. |
| `is_hardly_audible` | the voice is quiet (average level of the read words below -35 dBFS) or barely above the room noise (less than 10 dB louder) | `AUDIBLE_MIN_DBFS` -35, `AUDIBLE_MIN_SNR_DB` 10 | The noise level is taken from the silent gaps of at least 200 ms. If the browser applies automatic gain control, a quiet voice is boosted together with the noise, so the level alone looks fine and the noise comparison does the work. |

How well they match a person: on the 9 recordings the flags were compared with one person's yes/no judgements (`tests/fixtures/flags_labels.csv`; 36 judgements). `is_hardly_audible` agreed on all 9. `is_monotonous` agreed on 8, `is_word_by_word` on 6 and `is_disregard_punctuation` on 4. With 9 recordings this shows that a definition is clearly wrong, not what the exact threshold should be; the two punctuation and word-by-word results are the ones to review with a teacher. To see every number, run `uv run python devtools/flag_report.py` (writes `devtools/out/flags.md`). Each job also logs the four values and the measurements behind them (`flags computed`).

## Build the image

`audio/Dockerfile` (build context `audio/`, run from the repo root):

```
docker build -t readright-mai-audio audio/
```

- Base `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04` (cuDNN 9 for ctranslate2), Python 3.12 from uv (`pyproject.toml` allows below 3.13), `espeak-ng`, `ffmpeg` and `libsndfile1` from apt, torch from the cu126 index.
- The `models` build stage runs `devtools/fetch_models.py`: it downloads CrisperWhisper 2.0 large at the commit pinned in `models.lock`, converts it to CTranslate2 float16, fails the build if the `[verbatim_N]` tokens are missing, then downloads the phoneme model (pinned) and the whisperx English alignment model. The image needs network only at build time (`HF_HUB_OFFLINE=1` at runtime). Lane B is off by default but its model is baked in, so `AUDIO_PHONETIC_ENABLED=true` needs no rebuild.
- Runs as the non-root `mai` user on port 8001; the healthcheck calls `/health/ready` with `start-period=180s`.
- Run it with the NVIDIA container runtime (`docker run --gpus all ...`) or through compose, where `audio-worker` reserves one GPU.
- Download size: about 4.5 GB of models plus CUDA runtime, torch and the venv. Docker Desktop's disk image should be on a drive with 30 GB free.
- To prepare the same model folder without Docker (for a local test with `AUDIO_ASR_MODEL`, `AUDIO_PHONEME_MODEL` and `TORCH_HOME` pointing into it): `uv run python -m devtools.fetch_models --model-dir <dir>`.

The Dockerfile was written and its build step was run for real (`fetch_models`), but the image itself has not been built on a machine with Docker yet; that is verified on EC2 ([plans/phase-I2.md](plans/phase-I2.md) "Results").

### Machines without a GPU

`docker compose -f compose.yaml -f compose.override.yaml -f compose.fake-audio.yaml up --build` swaps the real audio worker for the server's fake one (canned results, no models, no GPU). Needs Docker Compose 2.24 or newer.

## Benchmark (dev machine, not deployment)

Measured 2026-11-02 with `devtools/benchmark.py` (real `AudioPipeline.run`, Lane B on, 9 recordings x 3 runs after one warm-up). Dev machine, not deployment. Excludes the server's download, WAV conversion, clip cutting and S3 upload.
Stage 5 is `PhoneticReviewer.review` minus stage 4 (it also covers passage prep, row adapting and write-back).

| Stage | median ms | p95 ms | max ms |
|---|---:|---:|---:|
| 1 WAV read | 77 | 110 | 126 |
| 2 ASR (transcribe + align) | 15144 | 21655 | 24082 |
| 3 Lane A rows | 307 | 353 | 366 |
| 4 Lane B recogniser | 894 | 1303 | 1365 |
| 5 Lane B join + compare + arbitrate | 19 | 24 | 27 |
| 6 post-pass + scoring | 0 | 0 | 1 |
| 7 total | 16377 | 23051 | 25863 |
| Lane A only (7 - 4 - 5) | 15545 | 22014 | 24481 |

- Total per second of audio: median 384 ms/s, p95 551, max 555 (Lane A only: median 363 ms/s)
- Peak VRAM: torch max_memory_allocated 2034 MB (reserved 2718 MB); whole-device peak in use 6144 MB (torch + CTranslate2); 5659 MB in use after load
- Model load: CrisperWhisper 2.0 large + alignment 14466 ms; Lane B recogniser 19501 ms
- 9 recordings x 3 runs = 27 timed runs, one warm-up not counted

Machine:
- GPU: NVIDIA GeForce GTX 1660 Ti, 6.0 GiB
- nvidia-smi after run: NVIDIA GeForce GTX 1660 Ti, 6144 MiB, 5641 MiB, 546.01 (name, total, used, driver)
- CUDA (torch build): 12.6; torch 2.8.0+cu126; ctranslate2 4.8.4; whisperx 3.8.6
- ASR_COMPUTE_TYPE=float16, ASR_BATCH_SIZE=8, PHONETIC_ENABLED=true, PHONEME_MODEL=facebook/wav2vec2-xlsr-53-espeak-cv-ft (fp16=False)
