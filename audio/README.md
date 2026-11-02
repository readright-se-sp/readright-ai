# audio

The audio lane of the ReadRight MAI. It implements the GO2 pipeline behind the worker contract in [../contracts/worker-api.md](../contracts/worker-api.md). Lane A (word lane, post-pass) and Lane B (phonetic lane, arbitration) are built by different developers; see [ROADMAP.md](ROADMAP.md).

Current state: Phases B0 and B1. The project scaffold, the shared, validated types (`src/audio/types.py`) and Stage 0 passage preparation (`PassagePreparer` in `src/audio/passage.py`, IPA via espeak-ng in `src/audio/phonetics/`) exist. There is no recogniser, API or audio processing yet.

## Layout

```
audio/
├── src/audio/        # package: settings.py, types.py, passage.py, phonetics/
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

### espeak-ng (system dependency)

Passage phonemes come from [espeak-ng](https://github.com/espeak-ng/espeak-ng), which `phonemizer` loads as a system library.

- Windows: install the `.msi` from the espeak-ng GitHub releases (or `winget install eSpeak-NG.eSpeak-NG`), then set `AUDIO_ESPEAK_LIBRARY=C:\Program Files\eSpeak NG\libespeak-ng.dll` in `audio/.env`. It is exported as `PHONEMIZER_ESPEAK_LIBRARY` before the backend is created.
- Linux/Docker: `apt-get install espeak-ng`.

Tests marked `espeak` are skipped, with a reason, when the library cannot be loaded. Other settings: `AUDIO_ESPEAK_LANGUAGE` (default `en-us`) and `AUDIO_PASSAGE_CACHE_SIZE` (default `128`).
