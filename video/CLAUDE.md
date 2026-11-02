# video/

Video lane of the MAI: answers `is_finger_pointing` and `is_lack_expression`. Root rules in [../CLAUDE.md](../CLAUDE.md) also apply. Setup and config: [README.md](README.md). Plan: [ROADMAP.md](ROADMAP.md). Contract: [../contracts/worker-api.md](../contracts/worker-api.md).

Work is done in phases: read [ROADMAP.md](ROADMAP.md) before planning or implementing anything.

## Commands

Run from `video/`. Python >= 3.11.

- `uv sync --extra dev`
- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format --check .` (`uv run ruff format .` to fix)
- `uv run uvicorn app.main:create_app --factory --reload --port 8002`

Run pytest, ruff check and ruff format --check before calling work done.

## Layout

Package `app/` at the top level (no `src/` layout, same as `server/`).

- `main.py`: `create_app()` factory, lifespan, routes.
- `config.py`: `Settings` (`MAI_VIDEO_*` env vars, `.env`).
- `models.py`: job/result/error models and `ErrorCode`.
- `errors.py`: `LaneError`, `STATUS_BY_CODE`, error handlers.
- `analyzer.py`: `Analyzer` protocol, `JobContext`, `JobCancelled`, `MediaPipeAnalyzer`.
- `frames.py`: `probe`, `plan_sampling`, `iter_frames` (ffprobe/ffmpeg to RGB frames).
- `landmarks.py`: `models.lock` reading/hashing, `ModelBundle`, `LandmarkerSession`, result mapping to `FrameFeatures`.
- `decision.py`: `decide()` turns the feature series into flags and stats (false/false until Phases 5-6).
- `jobs.py`: `JobRunner` (concurrency limit, queue, disconnect polling, stats).
- `logging.py`: JSON logs, `job_id_var`.
- `devtools/`: `fetch_models` (download models) and `benchmark_frames` (timing). Not part of the app.

## Conventions

- The app is built by `create_app(settings=None, analyzer=None)`. No global settings singleton.
- Errors: raise `LaneError(ErrorCode.X, message)`; the status comes from `STATUS_BY_CODE`. No ad-hoc error JSON.
- The analyzer is blocking and runs in a worker thread. It must call `ctx.check()` at least once per frame.
- ffmpeg runs as a subprocess and is killed and reaped in a `finally`, so cancel, deadline, error and generator close never leave it running.
- Never write or log frames, landmarks or video content.
- Settings use the `MAI_VIDEO_` prefix. Every new setting goes in `.env.example` and the README table.
- Use relative imports inside `app/`.

## Testing

- `conftest.py` isolates tests from `MAI_*` env vars and a local `.env`; use its fixtures.
- Use fakes (`FakeAnalyzer`), not real models. Tests that need ffmpeg, models or docker skip with a reason.

## Safety

- Never read or print `.env` files. Never commit.
