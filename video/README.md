# ReadRight video lane

The video lane of the MAI. It receives a job from the server (a recording on the shared scratch volume) and answers two flags. Contract: [../contracts/worker-api.md](../contracts/worker-api.md). Plan: [ROADMAP.md](ROADMAP.md).

- `is_finger_pointing`: whether the child points at the text with a finger while reading. How it works: filled in by Phases 5-6.
- `is_lack_expression`: whether the child's face stays expressionless while reading. How it works: filled in by Phases 5-6.

Current state (Phase 3): the full worker contract is implemented. Jobs are decoded to RGB frames (`app/frames.py`) and run through the MediaPipe face, hand and (optional) pose landmarkers (`app/landmarks.py`), but `decide()` (`app/decision.py`) still answers `false` for both flags until Phases 5-6.

## Prerequisites

- Python >= 3.11 and [uv](https://docs.astral.sh/uv/)
- ffmpeg and ffprobe on `PATH` (or set `MAI_VIDEO_FFMPEG_PATH` / `MAI_VIDEO_FFPROBE_PATH`):
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`

## Models

The MediaPipe model files are not committed. Download and hash-check them (listed in `models.lock`) into `MAI_VIDEO_MODEL_DIR`:

```
uv run python -m devtools.fetch_models
```

Without them the lane starts but `/health/ready` stays 503.

## Setup and run

```
uv sync --extra dev
uv run uvicorn app.main:create_app --factory --port 8002
```

To run the server against it, set `MAI_VIDEO_WORKER_URL=http://localhost:8002` on the server. The scratch directory must be shared between the server and the lane.

## API

`POST /v1/jobs`, `GET /health/ready` and `GET /stats`, as described in [../contracts/worker-api.md](../contracts/worker-api.md).

## Configuration

Settings come from `MAI_VIDEO_*` environment variables or `.env` (see `.env.example`). Env vars override `.env`.

| Setting | Default | Meaning |
|---|---|---|
| `MAI_VIDEO_LOG_LEVEL` | `INFO` | Log level. |
| `MAI_VIDEO_ALLOWED_MEDIA_DIR` | unset | If set, `webm_path` must resolve inside it. Unset accepts any absolute path. |
| `MAI_VIDEO_MAX_CONCURRENT_JOBS` | `2` | Analyses running at once (>= 1). Extra jobs wait. |
| `MAI_VIDEO_STATS_WINDOW` | `20` | Number of recent successful jobs averaged in `/stats` (>= 1). |
| `MAI_VIDEO_DISCONNECT_POLL_SECONDS` | `0.5` | How often a running job checks whether the client disconnected (> 0). |
| `MAI_VIDEO_FFMPEG_PATH` | `ffmpeg` | ffmpeg binary (name on `PATH` or absolute path). |
| `MAI_VIDEO_FFPROBE_PATH` | `ffprobe` | ffprobe binary. |
| `MAI_VIDEO_PROBE_TIMEOUT_SECONDS` | `10` | Time limit for each ffprobe call (> 0). |
| `MAI_VIDEO_SAMPLE_FPS` | `5.0` | Frames sampled per second; lowered for long videos so `MAX_FRAMES` holds (> 0). |
| `MAI_VIDEO_FRAME_WIDTH` | `640` | Decoded frame width in pixels, never above the source width (>= 64). |
| `MAI_VIDEO_MAX_FRAMES` | `900` | Most frames decoded per job (>= 1). |
| `MAI_VIDEO_MODEL_DIR` | `models` | Directory with the MediaPipe model files; relative paths resolve against the working directory. |
| `MAI_VIDEO_NUM_HANDS` | `2` | Most hands detected per frame (1-4). |
| `MAI_VIDEO_USE_POSE` | `false` | Also load and run the pose landmarker. |

## Tests and lint

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Benchmark

Times each landmarker per frame and the whole job on a recording, optionally comparing N parallel jobs in threads and in processes:

```
uv run python -m devtools.benchmark_frames <webm> [--parallel N] [--pose]
```

## Privacy

Recordings are of children. Never write or log frames, landmarks or video content, and never log `webm_path` contents.
