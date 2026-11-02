# audio

The audio lane of the ReadRight MAI. It implements the GO2 pipeline behind the worker contract in [../contracts/worker-api.md](../contracts/worker-api.md). Lane A (word lane, post-pass) and Lane B (phonetic lane, arbitration) are built by different developers; see [ROADMAP.md](ROADMAP.md).

Current state: Phases B0 to B2. The project scaffold, the shared, validated types (`src/audio/types.py`), Stage 0 passage preparation (`PassagePreparer` in `src/audio/passage.py`, IPA via espeak-ng in `src/audio/phonetics/`) and the whole-recording phoneme recogniser (`Wav2Vec2PhonemeRecognizer` in `src/audio/recognition/`) exist. There is no API yet.

## Layout

```
audio/
├── src/audio/        # package: settings.py, types.py, passage.py, errors.py, phonetics/, recognition/
├── devtools/         # phoneme_spike.py (output in devtools/out/, git-ignored)
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

Settings use the `AUDIO_` prefix; copy `.env.example` to `.env` to override them.

Tests marked `model` need the model weights (downloaded on first use) and tests marked `gpu` need CUDA; each is skipped with a reason when its requirement is missing.

### PyTorch and the phoneme model

- `torch` comes from the CUDA 12.6 wheel index (`pytorch-cu126` in `pyproject.toml`) on Windows and Linux; the wheels bundle their own CUDA runtime and need a driver from the 12.x series (528.33 or newer on Windows). torch 2.6 or newer is required, because `transformers` refuses to load the models' `pytorch_model.bin` on older versions. The wheel is about 2.5 GB, so check free disk space; set `UV_CACHE_DIR` to another drive if the default one is small.
- Model weights download on first use to the Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface`). Each candidate is about 1.3 GB. Set `HF_HOME` to relocate it. Baking the weights into the Docker image is B7.
- The default model is `facebook/wav2vec2-xlsr-53-espeak-cv-ft` in fp32 (`AUDIO_PHONEME_MODEL`, `AUDIO_PHONEME_FP16`); see [plans/phase-B2-spike.md](plans/phase-B2-spike.md).
- Spike: put the recordings in `data/`, then `uv run python devtools/phoneme_spike.py` (downloads Whisper small.en and both models, about 5 GB; takes about 15 minutes on a GTX 1660 Ti). Use `--summary-only` to rebuild `plans/phase-B2-spike.md` from `devtools/out/results.csv`.

### espeak-ng (system dependency)

Passage phonemes come from [espeak-ng](https://github.com/espeak-ng/espeak-ng), which `phonemizer` loads as a system library.

- Windows: install the `.msi` from the espeak-ng GitHub releases (or `winget install eSpeak-NG.eSpeak-NG`), then set `AUDIO_ESPEAK_LIBRARY=C:\Program Files\eSpeak NG\libespeak-ng.dll` in `audio/.env`. It is exported as `PHONEMIZER_ESPEAK_LIBRARY` before the backend is created.
- Linux/Docker: `apt-get install espeak-ng`.

On Windows, `build_g2p` uses `C:\Program Files\eSpeak NG\libespeak-ng.dll` when `AUDIO_ESPEAK_LIBRARY` is unset and the file exists, so no `.env` is needed on a default install. Tests marked `espeak` are skipped, with a reason, when the library cannot be loaded. Other settings: `AUDIO_ESPEAK_LANGUAGE` (default `en-us`) and `AUDIO_PASSAGE_CACHE_SIZE` (default `128`).
