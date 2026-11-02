# ReadRight video lane

The video lane of the MAI. It receives a job from the server (a recording on the shared scratch volume) and answers two flags. Contract: [../contracts/worker-api.md](../contracts/worker-api.md). Plan: [ROADMAP.md](ROADMAP.md).

- `is_finger_pointing`: whether the child points at the text with a finger while reading. How it works: see [How the finger-pointing check works](#how-the-finger-pointing-check-works).
- `is_lack_expression`: whether the child's face stays expressionless while reading. How it works: see [How the expression check works](#how-the-expression-check-works).

Current state (Phase 6): the full worker contract is implemented and the lane runs in Docker (`video/Dockerfile`, used by `compose.yaml`). Jobs are decoded to RGB frames (`app/frames.py`) and run through the MediaPipe face, hand and (optional) pose landmarkers (`app/landmarks.py`), and `decide()` (`app/decision.py`) decides `is_lack_expression` from the face blendshapes (`app/expression.py`) and `is_finger_pointing` from the hand (and optional pose) landmarks (`app/pointing.py`).

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

## Run with Docker

```
docker build -t readright-mai-video video/
```

The image bakes the models into `/models` at build time, runs as uid 1000 and listens on port 8002 with `MAI_VIDEO_ALLOWED_MEDIA_DIR=/scratch`. In the stack, run `docker compose up --build` from the repo root; the compose service mounts the shared `scratch` volume read-only and limits the lane to `MAI_VIDEO_CPUS` CPUs (default 4).

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
| `MAI_VIDEO_EXPRESSION_MIN_FACE_COVERAGE` | `0.5` | Share of frames that must show a face, otherwise the expression check is skipped and answers `false` (0-1). |
| `MAI_VIDEO_EXPRESSION_MIN_FACE_FRAMES` | `20` | Fewest frames with a face needed for the expression check (>= 1). |
| `MAI_VIDEO_EXPRESSION_SMOOTH_FRAMES` | `3` | Window of the median smoothing that removes one-frame spikes (odd, >= 1, at most `MIN_FACE_FRAMES`). |
| `MAI_VIDEO_EXPRESSION_MIN_RANGE` | `0.12` | A facial movement group must vary at least this much to count as expression (0-1). |
| `MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE` | `{}` | JSON object overriding the minimum for single groups, e.g. `{"smile": 0.2}`. Groups: `brow_raise`, `brow_down`, `smile`, `frown`, `squint`, `eye_wide`, `sneer`. Values 0-1. |
| `MAI_VIDEO_POINTING_MIN_HAND_SCORE` | `0.5` | Hand detections below this confidence never count as a pointing hand (0-1). |
| `MAI_VIDEO_POINTING_EXTENDED_MAX_BEND_DEG` | `35` | A finger counts as straight when both of its joints bend less than this many degrees (0-180). |
| `MAI_VIDEO_POINTING_CURLED_MIN_BEND_DEG` | `60` | A finger counts as curled when its middle joint bends at least this many degrees (0-180). |
| `MAI_VIDEO_POINTING_MERGE_GAP_MS` | `400` | Pointing moments closer together than this are joined into one episode (> 0). |
| `MAI_VIDEO_POINTING_MIN_EPISODE_MS` | `600` | Shortest pointing episode that counts (> 0). |
| `MAI_VIDEO_POINTING_MIN_TOTAL_MS` | `2000` | Least total pointing time that makes the flag `true` (> 0). |
| `MAI_VIDEO_POINTING_MIN_TOTAL_FRACTION` | `0.05` | Least share of the recording spent pointing; the larger of this and the total above applies (0-1). |
| `MAI_VIDEO_POINTING_USE_SWEEPS` | `true` | Also count a fingertip moving back and forth like tracking lines of text. |
| `MAI_VIDEO_SWEEP_MIN_MS` | `1000` | Shortest one-way fingertip movement that counts as a sweep (> 0). |
| `MAI_VIDEO_SWEEP_MIN_DX` | `0.10` | Least sideways distance of a sweep, as a share of the frame width (> 0, <= 1). |
| `MAI_VIDEO_SWEEP_RETURN_MAX_MS` | `1000` | Time after a sweep in which the fingertip must move back at least half the distance (> 0). |
| `MAI_VIDEO_SWEEP_MIN_COUNT` | `2` | Sweeps needed to make the flag `true` (>= 1). |
| `MAI_VIDEO_POINTING_POSE_MIN_VISIBILITY` | `0.5` | Pose cue: least visibility of the wrist (0-1). Only used with `USE_POSE`. |
| `MAI_VIDEO_POINTING_POSE_BOTTOM_BAND` | `0.3` | Pose cue: share of the frame height, from the bottom, where the wrist must be (0-1). Only used with `USE_POSE`. |

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

## How the expression check works

The face model gives, for every analysed frame, about fifty scores between 0 and 1 that say how much each part of the face is moving (eyebrows up, mouth corners raised, and so on). The check uses those scores only; it never looks at pictures.

Scores that reading itself moves are ignored: mouth and jaw movement (speech), blinking and where the eyes look. What is left is grouped into seven facial movements: raised brows, lowered brows, smile, frown, squint, wide eyes and nose sneer.

For each group the check follows its score over the recording, smooths it so a single odd frame is ignored, and measures how far it ranges between its low and high values (ignoring the extreme 5% at each end). If every group stays below its minimum range, the face was flat and `is_lack_expression` is `true`. If any group moves more than that, the child showed expression and the flag is `false`.

If a face is visible in too few frames (see the coverage and frame settings), there is not enough to judge and the flag is `false`. The job stats record `face_coverage`, `expression_reason` (`flat`, `expressive` or `insufficient_face`) and the measured range of each group (`expr_var_<group>`), which helps tune the minimums.

## How the finger-pointing check works

The hand model gives 21 points per hand (wrist, and four joints per finger). The check uses those points only; it never looks at pictures.

A hand counts as pointing when the index finger is straight, at least two of the middle, ring and little fingers are curled, and the model is confident enough about the hand. The thumb is ignored. Finger bends are measured from the hand's own 3D shape, so it does not matter how the hand is turned toward the camera.

Single frames are not enough. Frames with a pointing hand are joined into episodes (short gaps are bridged), episodes that are too short are dropped, and the flag is `true` when the kept episodes add up to enough time: at least `POINTING_MIN_TOTAL_MS`, or `POINTING_MIN_TOTAL_FRACTION` of the recording if that is longer.

The flag is also `true` when the fingertip sweeps sideways and back at least `SWEEP_MIN_COUNT` times, like a finger following lines of text. This works even when the hand shape is not clear, and can be turned off with `POINTING_USE_SWEEPS`.

With `USE_POSE` on, a second cue helps when the hand is out of view: an arm reaching toward the screen (wrist low in the frame, near the shoulders, elbow below it) while no hand is detected. This pose cue is off by default because the pose model is not loaded unless `USE_POSE` is set.

Known false positives: a hand resting on the chin, a hand gripping a tablet at the frame edges, and a parent's hand pointing in the frame. The job stats record `pointing_frames`, `pointing_episodes`, `pointing_total_ms`, `sweeps`, `pose_frames` and `pointing_reason` (`shape`, `sweeps`, `both`, `none` or `no_hands`), which helps tune the settings.

## Privacy

Recordings are of children. Never write or log frames, landmarks or video content, and never log `webm_path` contents.
