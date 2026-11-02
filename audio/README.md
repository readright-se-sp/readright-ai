# audio

The audio lane of the ReadRight MAI. It implements the GO2 pipeline behind the worker contract in [../contracts/worker-api.md](../contracts/worker-api.md). Lane A (word lane, post-pass) and Lane B (phonetic lane, arbitration) are built by different developers; see [ROADMAP.md](ROADMAP.md).

Current state: Lane A (CrisperWhisper word lane, post-pass, scoring) and Lane B (phoneme recogniser, join, compare, arbitration) are integrated in one pipeline (Phase I1): `AudioPipeline` in `src/audio/pipeline.py`, composed in `src/audio/wiring.py`. Lane B plugs in as `PhoneticReviewer` (`src/audio/lane_b/reviewer.py`) and adds an optional `phonetic` object to each row it judged (contract v5, [../contracts/audio-service.md](../contracts/audio-service.md)). `AUDIO_PHONETIC_ENABLED=false` runs Lane A alone. There is no HTTP layer yet (Phase I2). Calibration of Lane B on the integrated pipeline is B6; until then the Phase I1 results show it over-flags short words (see [plans/phase-I1.md](plans/phase-I1.md), "Results").

## Layout

```
audio/
├── src/audio/        # package: pipeline.py, wiring.py, models.py, config.py (settings), alignment/, tagging/, transcription/ (Lane A); types.py, passage.py, phonetics/, recognition/, lane_b/, arbitration/ (Lane B)
├── models/           # converted CrisperWhisper (git-ignored)
├── devtools/         # run_pipeline.py (full pipeline on data/), run_lane_b.py, phoneme_spike.py (output in devtools/out/, git-ignored)
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
- Model weights download on first use to the Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface`). Each candidate is about 1.3 GB. Set `HF_HOME` to relocate it. Baking the weights into the Docker image is B7.
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
