# audio

The audio lane of the ReadRight MAI. It implements the GO2 pipeline behind the worker contract in [../contracts/worker-api.md](../contracts/worker-api.md). Lane A (word lane, post-pass) and Lane B (phonetic lane, arbitration) are built by different developers; see [ROADMAP.md](ROADMAP.md).

Current state: Phase B0. The project scaffold and the shared, validated types (`src/audio/types.py`) exist. There is no ML, API or audio processing yet.

## Layout

```
audio/
├── src/audio/        # package: settings.py, types.py
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
