# ReadRight video lane

The video lane of the MAI. It receives a job from the server (a recording on the shared scratch volume) and answers two flags. Contract: [../contracts/worker-api.md](../contracts/worker-api.md). Plan: [ROADMAP.md](ROADMAP.md).

- `is_finger_pointing`: whether the child points at the text with a finger while reading. How it works: filled in by Phases 5-6.
- `is_lack_expression`: whether the child's face stays expressionless while reading. How it works: filled in by Phases 5-6.

Current state (Phase 1): the full worker contract is implemented with a placeholder analyzer that always answers `false` for both flags.

## Prerequisites

- Python >= 3.11 and [uv](https://docs.astral.sh/uv/)
- ffmpeg (needed from Phase 2)

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

## Tests and lint

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Privacy

Recordings are of children. Never write or log frames, landmarks or video content, and never log `webm_path` contents.
