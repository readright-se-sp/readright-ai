# Video lane roadmap

The video lane is built in 7 phases. Each phase below says what to build, the interfaces, numbered acceptance criteria, the files it touches, what is out of scope, and the questions still open (each with a recommended answer). A new session should be able to write a phase brief from its section plus the sources listed under "Before planning".

## Status

| # | Phase | Status |
|---|---|---|
| 1 | Project setup and worker contract layer | Done |
| 2 | Frame decoding (ffprobe + ffmpeg) | Done |
| 3 | MediaPipe landmarks and models | Done |
| 4 | Packaging and compose | Not started |
| 5 | Lack of expression | Not started |
| 6 | Finger pointing | Not started |
| 7 | Calibration, benchmark and docs | Not started |

## How to work a phase

1. **Before planning**, read: this file, [CLAUDE.md](CLAUDE.md) (from Phase 2 on), [../CLAUDE.md](../CLAUDE.md), [the worker contract](../contracts/worker-api.md), [the deployment doc](../docs/deployment-architecture.md), and the current code in `app/`, `devtools/` and `tests/`. The server's lane client ([../server/app/workers.py](../server/app/workers.py)) and fake video worker ([../server/devtools/fake_workers.py](../server/devtools/fake_workers.py)) show what the server sends and expects.
2. **Draft a plan** for the user: files to add or change, new settings, new dependencies, behavior and error mapping, tests, done criteria, and the phase's open questions with a recommended answer for each. Do not resolve an open question silently.
3. **Wait** for the user's approval and an explicit go. Never start a phase, or the next one, unprompted.
4. **Implement**, then run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` from `video/`.
5. **Finish**: mark the phase done in the status table above; add new settings to `.env.example` and the configuration table in [README.md](README.md); update [README.md](README.md) ("Current state" and any section the phase changes) and [CLAUDE.md](CLAUDE.md) (layout, conventions); update [../README.md](../README.md), [../CLAUDE.md](../CLAUDE.md) and [../docs/deployment-architecture.md](../docs/deployment-architecture.md) if behavior changed; record decisions made during the phase under "Decisions" in its section below.

## What the lane must do

The contract ([../contracts/worker-api.md](../contracts/worker-api.md), v1.0.0) is fixed; no phase changes it.

- `POST /v1/jobs` with `{"job_id", "deadline", "webm_path"}` returns `{"job_id", "is_finger_pointing", "is_lack_expression"}`.
- `is_lack_expression`: little or no change of facial expression throughout the recording.
- `is_finger_pointing`: a finger was seen pointing (at the screen or the text), or the reader's movements show they were tracking the text with a finger.
- Errors (body `{"error": {"code", "message"}}`): 400 `INVALID_JOB`, 422 `MEDIA_UNREADABLE`, 504 `DEADLINE_EXCEEDED`, 500 `INTERNAL`. The video lane never returns `NO_SPEECH`.
- `GET /health/ready`: 200 `{"status": "ready"}` once the models are loaded, else 503.
- `GET /stats`: 200 `{"queue_length": int, "avg_processing_seconds": float | null}`.
- Drop a job whose deadline has passed (504); stop work when the server closes the connection.
- Read the webm from the shared scratch volume (read-only); never write to it; never touch S3, the BE or the audio lane.

The server fails the whole request when the video lane fails (unless `MAI_ALLOW_AUDIO_ONLY_ON_VIDEO_FAILURE`), so the lane returns `false` rather than an error when it simply cannot see enough (no face, no hands).

## Target job flow

Where each phase hooks into `POST /v1/jobs` once everything is built:

1. Validate the body → 400 `INVALID_JOB` — Phase 1
2. Deadline already passed → 504 `DEADLINE_EXCEEDED`, no work — Phase 1
3. File missing or outside the allowed directory → 422 / 400 — Phase 1
4. Wait for a free slot (`MAI_VIDEO_MAX_CONCURRENT_JOBS`); counted in `queue_length` — Phase 1
5. Probe the webm, decode frames at a fixed sample rate through an ffmpeg pipe → 422 `MEDIA_UNREADABLE` on failure — Phase 2
6. Run the MediaPipe face, hand (and optional pose) landmarkers on each frame → per-frame features — Phase 3
7. Decide `is_lack_expression` — Phase 5; decide `is_finger_pointing` — Phase 6
8. Respond 200; record the job time for `/stats` — Phase 1
9. Deadline passes or the connection closes during 5-7 → stop decoding (kill ffmpeg) and inference; 504 or nothing — Phases 1-2

## Technical choices (apply to every phase)

| Piece | Choice | Why |
|---|---|---|
| Project | Independent Python project in `video/` (own `pyproject.toml`, `uv.lock`, `app/`, `tests/`, `devtools/`), shares no code with `server/` | Root rule: projects share no code |
| Python | `>=3.11`, tested on 3.13 (image `python:3.13-slim-bookworm`, same as server) | mediapipe 1.x ships `py3-none` wheels for manylinux x86_64/aarch64, Windows and macOS arm64 |
| Web | FastAPI + uvicorn, one process, port 8002 | Same as server; matches `MAI_VIDEO_WORKER_URL` |
| Models | MediaPipe Tasks `FaceLandmarker` (blendshapes on), `HandLandmarker`, optional `PoseLandmarker` lite | Small, Apache 2.0, CPU-real-time; blendshapes give expression directly |
| Device | CPU (XNNPACK) | Keeps the A10G for CrisperWhisper; MediaPipe Python GPU needs EGL inside the container |
| Decoding | `ffprobe` + `ffmpeg` subprocess writing raw RGB frames to a pipe at a fixed fps | Browser MediaRecorder webm is variable-frame-rate and often has no duration header; ffmpeg handles both and can be killed on cancel |
| Running mode | MediaPipe `RunningMode.VIDEO`, new landmarker instances per job | VIDEO mode keeps tracking state per stream, so instances cannot be shared between jobs |
| Settings | pydantic-settings, prefix `MAI_VIDEO_` | Consistent with server's `MAI_` |
| Lint | ruff E, F, I, B, UP; line length 100; target py311 | Same as server |

Privacy: recordings are of children. The lane never writes frames, crops, landmarks or video to disk and never logs them. It logs only aggregate numbers (frame counts, coverage, episode counts, variability). Debug tools that render frames (Phase 7) are for local use with consented clips only and are not part of the image.

---

## Phase 1 — Project setup and worker contract layer

**Goal.** A runnable `video/` project that implements the whole worker contract with a placeholder analyzer that always answers `false/false`, plus the project's README and CLAUDE.md. After this phase the server can run end to end against the real lane process (outside Docker).

**Build.**
- `pyproject.toml` (hatchling, `app` package, version in `app/__init__.py` = `0.1.0`), dependencies `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`; dev extra `pytest`, `ruff`, `httpx`. `uv.lock` generated. `.gitignore` (`.venv`, caches, `models/`), `.env.example`.
- `app/config.py`: `Settings(BaseSettings)` with prefix `MAI_VIDEO_`, reads `.env`, env vars override.
- `app/models.py`: `VideoJob` (`job_id: str`, `deadline: int`, `webm_path: str`; unknown extra fields ignored), `VideoResult` (`job_id: str`, `is_finger_pointing: bool`, `is_lack_expression: bool`), `ErrorCode` enum (`INVALID_JOB`, `MEDIA_UNREADABLE`, `DEADLINE_EXCEEDED`, `INTERNAL`), `ErrorBody`.
- `app/errors.py`: `LaneError(code, message)`, `STATUS_BY_CODE` (400, 422, 504, 500), exception handlers. Request validation errors → 400 `INVALID_JOB` without echoing input. Any other `HTTPException` (404, 405) → that status with code `INVALID_JOB`. Unhandled exceptions → 500 `INTERNAL` with a generic message (details only in the log).
- `app/analyzer.py`:
  - `VideoFlags` frozen dataclass (`is_finger_pointing: bool`, `is_lack_expression: bool`).
  - `JobContext` (`job_id: str`, `deadline_ms: int`, `cancel_event: threading.Event`) with `check() -> None` that raises `JobCancelled` if the event is set and `LaneError(DEADLINE_EXCEEDED)` if `now_ms >= deadline_ms`.
  - `Analyzer` Protocol: `def load(self) -> None` (blocking; loads models), `@property ready -> bool`, `def analyze(self, webm_path: Path, ctx: JobContext) -> VideoFlags` (blocking, runs in a worker thread, calls `ctx.check()` regularly; raises `LaneError(MEDIA_UNREADABLE, ...)` for undecodable media).
  - `PlaceholderAnalyzer`: `load()` sets ready, `analyze()` calls `ctx.check()` once and returns `VideoFlags(False, False)`. Replaced in Phase 3.
- `app/jobs.py`: `JobRunner(settings, analyzer)`: an `asyncio.Semaphore(max_concurrent_jobs)`, `queue_length` (jobs waiting for a slot), a rolling window of the last `stats_window` successful job durations. `async run(job, is_disconnected) -> VideoFlags` runs `analyzer.analyze` via `asyncio.to_thread`, and in parallel polls `is_disconnected()` every `disconnect_poll_seconds`; on disconnect it sets `cancel_event` and returns once the thread stops. It re-checks the deadline after acquiring the slot (504 if it passed while queued).
- `app/main.py`: `create_app(settings=None, analyzer=None) -> FastAPI`. Lifespan: `app.state.settings`, `app.state.analyzer` (`PlaceholderAnalyzer` unless injected), runs `analyzer.load()` in a thread **as a background task** (startup does not block, so `/health/ready` answers 503 while loading), `app.state.jobs`. Routes `POST /v1/jobs`, `GET /health/ready`, `GET /stats`. JSON logging with `job_id` on each job log line; one line per finished job with status and duration.
- `tests/conftest.py`: strips `MAI_*` env vars and ignores any `.env` (same idea as server's), fixtures `settings`, `client`, a `webm_file` (an empty file under `tmp_path`; Phase 1 does not decode), a `FakeAnalyzer` helper usable by later phases.
- `README.md` and `CLAUDE.md` for `video/` (see "Docs" below).
- Root updates: `../CLAUDE.md` (workflow table row for `video/` → this roadmap; layout line "video/: video lane, see video/CLAUDE.md"), `../README.md` (`video/` row in the project table: MediaPipe face and hand landmarks; "Current state").

**Interfaces.**

```python
# app/main.py
def create_app(settings: Settings | None = None, analyzer: Analyzer | None = None) -> FastAPI: ...
# app/jobs.py
class JobRunner:
    def __init__(self, settings: Settings, analyzer: Analyzer) -> None: ...
    @property
    def queue_length(self) -> int: ...
    @property
    def avg_processing_seconds(self) -> float | None: ...
    async def run(self, job: VideoJob, is_disconnected: Callable[[], Awaitable[bool]]) -> VideoFlags: ...
```

**Settings.** `MAI_VIDEO_LOG_LEVEL` (`INFO`), `MAI_VIDEO_ALLOWED_MEDIA_DIR` (unset = any absolute path; compose sets `/scratch`), `MAI_VIDEO_MAX_CONCURRENT_JOBS` (2, ≥ 1), `MAI_VIDEO_STATS_WINDOW` (20, ≥ 1), `MAI_VIDEO_DISCONNECT_POLL_SECONDS` (0.5, > 0).

**Acceptance criteria.**
1. `POST /v1/jobs` with a valid body and an existing file returns 200 `{"job_id": <same>, "is_finger_pointing": false, "is_lack_expression": false}` with exactly those three keys.
2. The response `job_id` equals the request's `job_id` exactly (including case).
3. Missing field, wrong type, non-JSON body or non-object body → 400 `{"error": {"code": "INVALID_JOB", "message": ...}}`; the message does not contain the submitted `webm_path` or `job_id`. Unknown extra fields are ignored (200).
4. `webm_path` that is relative, or (when `MAI_VIDEO_ALLOWED_MEDIA_DIR` is set) resolves outside that directory (including via `..`), → 400 `INVALID_JOB`; the analyzer is not called.
5. `webm_path` that does not exist or is not a regular file → 422 `MEDIA_UNREADABLE`; the analyzer is not called.
6. `deadline` ≤ now (epoch ms) → 504 `DEADLINE_EXCEEDED`; the analyzer is not called. The deadline check runs before the file check.
7. An analyzer raising `LaneError(MEDIA_UNREADABLE)` → 422; raising `LaneError(DEADLINE_EXCEEDED)` → 504; raising any other exception → 500 `INTERNAL` with a message that does not contain the exception text.
8. Unknown route → 404 and wrong method → 405, both with the `{"error": {...}}` body.
9. `GET /health/ready` → 503 while `analyzer.ready` is false (load still running or failed), 200 `{"status": "ready"}` once it is true. A `load()` that raises leaves the lane not ready and logs the error; the app keeps serving.
10. `GET /stats` → `{"queue_length": 0, "avg_processing_seconds": null}` before any job; after jobs finish, `avg_processing_seconds` is the mean duration of the last `MAI_VIDEO_STATS_WINDOW` successful jobs. Failed jobs are not counted.
11. At most `MAI_VIDEO_MAX_CONCURRENT_JOBS` analyses run at once; extra jobs wait and `queue_length` counts them while they wait (tested with a blocking fake analyzer).
12. A job whose deadline passes while it waits for a slot → 504 without calling the analyzer.
13. When `is_disconnected()` turns true during a job, `ctx.cancel_event` is set within `2 × MAI_VIDEO_DISCONNECT_POLL_SECONDS` and the slot is released once the analyzer returns or raises `JobCancelled` (tested on `JobRunner` directly with a fake `is_disconnected`).
14. `JobContext.check()` raises `JobCancelled` when the event is set and `LaneError(DEADLINE_EXCEEDED)` when the deadline has passed; otherwise returns.
15. Settings: defaults as listed; invalid values (`MAX_CONCURRENT_JOBS=0`, negative poll) fail at startup; env vars override `.env`.
16. `README.md`, `CLAUDE.md`, `.env.example` exist; every `MAI_VIDEO_*` setting in `Settings` appears in `.env.example` and in the README configuration table (a test compares the field names).
17. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` pass.

**Docs (this phase writes them).**
- `video/README.md`: what the lane does and its two flags (one paragraph each, "how it works" filled in by Phases 5-6); "Current state (Phase N)" line; prerequisites (Python, uv; ffmpeg from Phase 2); setup (`uv sync --extra dev`); run (`uv run uvicorn app.main:create_app --factory --port 8002`); running the server against it (`MAI_VIDEO_WORKER_URL=http://localhost:8002`, scratch dir must be shared); API summary with a link to the contract; configuration table (setting, default, meaning); tests and lint commands; privacy section.
- `video/CLAUDE.md`, in the style of `server/CLAUDE.md`: one-line purpose and links (README, ROADMAP, contract); "Work is done in phases: read ROADMAP.md first"; commands; layout (one line per module); conventions (`create_app(settings, analyzer)` factory, no global settings; errors only via `LaneError` + `STATUS_BY_CODE`; analyzer is blocking and runs in a thread, calls `ctx.check()` at least once per frame; never write or log frames/landmarks; settings `MAI_VIDEO_` prefix and the "every new setting goes in `.env.example` and the README table" rule; relative imports in `app/`); testing (conftest isolation, fakes not real models, ffmpeg/model/docker-dependent tests skip with a reason); safety (no `.env` reading, no committing).

**Files.** Create: `video/pyproject.toml`, `video/uv.lock`, `video/.gitignore`, `video/.env.example`, `video/README.md`, `video/CLAUDE.md`, `video/app/__init__.py`, `video/app/config.py`, `video/app/models.py`, `video/app/errors.py`, `video/app/analyzer.py`, `video/app/jobs.py`, `video/app/main.py`, `video/app/logging.py`, `video/tests/conftest.py`. Modify: `../CLAUDE.md`, `../README.md`, this file (status, decisions).

**Out of scope.** Decoding, MediaPipe, Dockerfile/compose, any change to `server/` or the contract.

**Open questions.**
- Restrict `webm_path` to a directory? Recommendation: yes, optional `MAI_VIDEO_ALLOWED_MEDIA_DIR` (set to `/scratch` in compose, unset locally), because the contract says paths are on the scratch volume and it stops the lane being used to read arbitrary files. A violation is 400 `INVALID_JOB` (the body is wrong), not 422.
- Order of checks: deadline before file existence? Recommendation: validation → deadline → path → file, so an expired job never touches the disk.
- Cancellation detection: polling `request.is_disconnected()` vs. a pure-ASGI receive-channel watcher. Recommendation: polling every 0.5 s; simple and testable, cost negligible.
- Should the lane also expose `/health/live`? Recommendation: no; the contract and the compose healthcheck use `/health/ready` only.

**Done when.** The criteria pass; the server (run locally with `MAI_VIDEO_WORKER_URL=http://localhost:8002` and a shared scratch dir) completes a request with this lane instead of the fake.

**Decisions.**
- `webm_path` may be restricted with the optional `MAI_VIDEO_ALLOWED_MEDIA_DIR`; a path outside it, or one that cannot be resolved (NUL byte, symlink loop), is 400 `INVALID_JOB`. The path is resolved strictly, so symlinks that escape the directory are rejected.
- Check order: validation → deadline → path → file. An expired job never touches the disk.
- Disconnects are detected by polling `request.is_disconnected()` every `MAI_VIDEO_DISCONNECT_POLL_SECONDS` in an asyncio task beside the analysis thread. The poller is cancelled as soon as the analysis ends, so it adds no latency. A failing check counts as a disconnect.
- A client that disconnects while its job is queued (including at the moment a slot frees) drops the job; the analyzer is never called.
- A disconnected job answers 500 `INTERNAL` (nobody reads it) and logs `status=cancelled`; it is not counted in `/stats`.
- If the request task is cancelled (e.g. shutdown) during an analysis, `cancel_event` is set and the slot stays held until the analyzer thread ends, so `MAX_CONCURRENT_JOBS` is never exceeded.
- No `/health/live`. `/health/ready` 503 uses the standard error body (`INTERNAL`, "Models are not loaded.").
- `VideoJob` uses strict types: `job_id` a non-empty string, `deadline` an integer (`"123"`, `1.5` and `true` are 400).
- `configure_logging` replaces only its own JSON handler, so other root handlers (e.g. pytest's caplog) survive `create_app`.
- `ruff format` excludes `*.md`, because recent ruff also formats Python code blocks in Markdown (this file).

---

## Phase 2 — Frame decoding (ffprobe + ffmpeg)

**Goal.** Turn a browser-recorded webm into a bounded, evenly spaced stream of RGB frames with exact timestamps, killable at any moment.

**Build.**
- `app/frames.py`:
  - `VideoInfo` (frozen): `width`, `height` (display size, after rotation), `rotation` (0, 90, 180, 270), `duration_ms: int | None`.
  - `probe(path, settings) -> VideoInfo`: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration:stream_side_data=rotation:stream_tags=rotate:format=duration -of json`. Duration from the stream, else the format, else (MediaRecorder files often have none) the last video packet timestamp from a demux-only pass (`-show_entries packet=pts_time`). Width/height swapped when rotation is ±90/270. No video stream, ffprobe non-zero exit, unparsable output or timeout → `LaneError(MEDIA_UNREADABLE)`.
  - `SamplePlan` (frozen): `fps: float`, `width: int`, `height: int`, `max_frames: int`. `plan_sampling(info, settings) -> SamplePlan`: `fps = min(SAMPLE_FPS, MAX_FRAMES * 1000 / duration_ms)` when the duration is known, else `SAMPLE_FPS`; `width = min(FRAME_WIDTH, info.width)` rounded down to even; `height` keeps the aspect ratio, rounded to even, ≥ 2.
  - `Frame` (frozen): `index: int`, `timestamp_ms: int`, `rgb: numpy.ndarray` (shape `(height, width, 3)`, `uint8`).
  - `iter_frames(path, plan, ctx, settings) -> Iterator[Frame]`: runs `ffmpeg -nostdin -v error -i <path> -an -sn -vf fps=<fps>,scale=<w>:<h> -pix_fmt rgb24 -f rawvideo pipe:1` (ffmpeg auto-rotates), reads exactly `w*h*3` bytes per frame, `timestamp_ms = round(index * 1000 / fps)` (strictly increasing), stops after `max_frames`, calls `ctx.check()` before every frame. The process is killed and reaped when the generator is closed, on cancel, on deadline and on error (a `try/finally`). A trailing partial frame is discarded.
  - Errors: zero frames decoded → `MEDIA_UNREADABLE`. ffmpeg exits non-zero after ≥ 1 frame (truncated upload) → keep the frames, log a warning. ffmpeg/ffprobe binary missing → `LaneError(INTERNAL)`.
- Dependency: `numpy`.
- Test media generated at test time with ffmpeg (`testsrc`, VP8 and VP9 in webm, one with a rotation tag, one without a duration, one truncated, one audio-only, a text file renamed `.webm`); tests skip with a clear reason if ffmpeg is not on PATH. No committed media.

**Settings.** `MAI_VIDEO_FFMPEG_PATH` (`ffmpeg`), `MAI_VIDEO_FFPROBE_PATH` (`ffprobe`), `MAI_VIDEO_PROBE_TIMEOUT_SECONDS` (10), `MAI_VIDEO_SAMPLE_FPS` (5.0, > 0), `MAI_VIDEO_FRAME_WIDTH` (640, ≥ 64), `MAI_VIDEO_MAX_FRAMES` (900, ≥ 1).

**Acceptance criteria.**
1. `probe` returns width, height and duration for a VP8 and a VP9 webm made by ffmpeg.
2. A webm with a 90° rotation reports swapped display width/height and `rotation == 90`; the frames from `iter_frames` have the display orientation.
3. A webm without a duration in its header still gets `duration_ms` (from packets), within 1 frame of the real length.
4. Audio-only webm, a non-media file, an empty file → `LaneError` with `MEDIA_UNREADABLE`.
5. `plan_sampling`: 10 s video → fps = `SAMPLE_FPS`; a video longer than `MAX_FRAMES / SAMPLE_FPS` seconds → fps lowered so the frame count is ≤ `MAX_FRAMES`; unknown duration → `SAMPLE_FPS`; width never above the source width; width and height even; aspect ratio kept within 1 px.
6. `iter_frames` on a 4 s, 30 fps clip at `SAMPLE_FPS=5` yields 20 ± 1 frames of the planned shape and dtype, with strictly increasing `timestamp_ms` equal to `round(i * 1000 / fps)`.
7. Never yields more than `max_frames` frames.
8. Setting `ctx.cancel_event` mid-iteration raises `JobCancelled` at the next frame and the ffmpeg process has exited (no zombie; checked through a process handle exposed for tests or by patching `subprocess.Popen`).
9. A deadline passing mid-iteration raises `LaneError(DEADLINE_EXCEEDED)` and kills ffmpeg.
10. Closing the generator early (consumer `break`) kills ffmpeg.
11. A truncated webm that yields some frames returns them without raising; one that yields none raises `MEDIA_UNREADABLE`.
12. A missing ffmpeg/ffprobe binary (bad `*_PATH`) → `LaneError(INTERNAL)`.
13. The exact ffmpeg arguments match the build section (tested by patching the process launcher, so it runs without ffmpeg).
14. New settings validated and documented (`.env.example`, README table); README prerequisites list ffmpeg with install commands for Windows (`winget install Gyan.FFmpeg`), macOS (`brew install ffmpeg`) and Debian/Ubuntu.

**Files.** Create: `video/app/frames.py`. Modify: `video/app/config.py`, `video/pyproject.toml`, `video/uv.lock`, `video/.env.example`, `video/README.md`, `video/CLAUDE.md`, this file.

**Out of scope.** MediaPipe, wiring frames into the analyzer (Phase 3), Docker.

**Open questions.**
- Truncated webm (ffmpeg error after some frames): use what decoded, or 422? Recommendation: use the frames and log a warning; a cut-off upload still shows the reader.
- PyAV instead of an ffmpeg subprocess? Recommendation: subprocess; same tool as the server, no extra native wheel, trivially killable, and ffmpeg handles VFR/missing-duration webm well.
- `fps` filter semantics: ffmpeg's `fps` filter picks the frame nearest to each output tick, so timestamps are synthetic (`i/fps`). Recommendation: accept; the analyses only need even spacing.

**Done when.** The criteria pass with ffmpeg installed, and skip cleanly without it.

**Decisions.**
- ffmpeg runs as a subprocess (no PyAV); `subprocess.Popen` is looked up at call time so tests can patch it. ffprobe uses `subprocess.run` with `probe_timeout_seconds`.
- A truncated webm that decoded some frames keeps them and logs a warning (exit code and frame count only); zero frames is 422 `MEDIA_UNREADABLE`.
- Timestamps are synthetic, `round(i * 1000 / fps)`, from the `fps` filter.
- ffmpeg stderr goes to DEVNULL (no drain thread); stderr and the path are never logged.
- The pipe read blocks, so `ctx.check()` runs only between frames (no reader thread).
- A rotation that is not a multiple of 90 is rounded to the nearest 90 and normalized to 0/90/180/270.
- Missing duration: the format duration, then the largest packet `pts_time` from a demux-only pass, else `None` (fps stays `SAMPLE_FPS`).
- The warning for a non-zero ffmpeg exit uses the real exit code read at end of stream; a process killed by us is never reported.
- ffprobe/ffmpeg binary missing is 500 `INTERNAL`; ffprobe timeout is `MEDIA_UNREADABLE`.

---

## Phase 3 — MediaPipe landmarks and models

**Goal.** Replace the placeholder with a `MediaPipeAnalyzer` that decodes frames (Phase 2), runs the landmarkers on each frame and produces a per-frame feature series. The two flags still come from a `decide()` that returns `false/false` until Phases 5 and 6. Measure speed and pick threads vs processes.

**Build.**
- Dependency: `mediapipe` pinned to an exact 1.x version (≥ 2 weeks old at the time of the phase; 1.2.0 was released 2026-10-27). Verify the Tasks import path for 1.x (1.0 changed Tasks API namespacing) and record it in Decisions.
- `models.lock` (repo file, `video/models.lock`): one line per model, `name url sha256`, for `face_landmarker.task`, `hand_landmarker.task`, `pose_landmarker_lite.task` (pinned `storage.googleapis.com/mediapipe-models/...` URLs, not `latest` if a versioned URL exists).
- `devtools/fetch_models.py`: downloads each model in `models.lock` into `MAI_VIDEO_MODEL_DIR` (default `models/`, git-ignored), verifies SHA-256, skips files already present with the right hash, fails on mismatch. Used by developers and by the Dockerfile (Phase 4).
- `app/landmarks.py`:
  - `FaceSample(blendshapes: dict[str, float])`, `HandSample(world: ndarray (21, 3), image: ndarray (21, 3) normalized x/y/z, handedness: str, score: float)`, `PoseSample(landmarks: ndarray (33, 4) normalized x, y, z, visibility)`, `FrameFeatures(timestamp_ms: int, face: FaceSample | None, hands: list[HandSample], pose: PoseSample | None)`.
  - `ModelBundle(settings)`: checks the model files exist and match `models.lock` hashes; `validate()` creates and closes one landmarker of each kind (pose only when `USE_POSE`) so a broken model fails at load, not at the first job.
  - `LandmarkerSession(bundle, settings)`: context manager that creates fresh VIDEO-mode landmarkers for one job (face: `num_faces=1`, `output_face_blendshapes=True`; hands: `num_hands=MAI_VIDEO_NUM_HANDS`; pose only when `USE_POSE`), `process(frame) -> FrameFeatures`, closes everything on exit.
- `app/decision.py`: `Decision(flags: VideoFlags, stats: dict[str, float | int | str])`; `decide(features: list[FrameFeatures], plan: SamplePlan, settings) -> Decision`. This phase: `false/false`, stats `frames`, `face_frames`, `hand_frames`. Phases 5 and 6 fill it in.
- `app/analyzer.py`: `MediaPipeAnalyzer(settings)`: `load()` builds and validates the `ModelBundle`; `analyze()` = `probe` → `plan_sampling` → `iter_frames` → `session.process` per frame (with `ctx.check()` per frame) → `decide` → log `decision.stats` (no landmarks) → return `decision.flags`. It becomes the default in `create_app`; `PlaceholderAnalyzer` is kept for tests only (move it to the test helpers if nothing else uses it).
- `devtools/benchmark_frames.py`: for a given webm, prints ms/frame for face, hands and pose separately and total job time; with `--parallel N` runs N jobs in threads and in processes and prints the wall time for each, to decide whether MediaPipe releases the GIL.
- Concurrency choice from the benchmark: if threads scale (N jobs in threads ≈ N jobs in processes), keep `asyncio.to_thread`; otherwise `JobRunner` dispatches to a `ProcessPoolExecutor` of `MAX_CONCURRENT_JOBS` workers, each loading the bundle once in its initializer, with cancellation through a `multiprocessing` event. Record the measured numbers and the choice in Decisions.

**Settings.** `MAI_VIDEO_MODEL_DIR` (`models`), `MAI_VIDEO_NUM_HANDS` (2, 1-4), `MAI_VIDEO_USE_POSE` (false).

**Acceptance criteria.**
1. `fetch_models.py` downloads into the model dir and verifies hashes; an existing correct file is not re-downloaded; a hash mismatch fails with a non-zero exit and removes the bad file (tested with a local HTTP fixture or patched download, no internet).
2. `ModelBundle` raises a clear error naming the missing or mismatched file; the lane then stays not ready (`/health/ready` 503).
3. With the real models present (tests skip otherwise): `load()` succeeds and `/health/ready` becomes 200.
4. With the real models present: `analyze()` on an ffmpeg `testsrc` webm (no face, no hands) returns `false/false`; every `FrameFeatures` has `face is None` and `hands == []`; timestamps match the sample plan.
5. With the real models present: `LandmarkerSession` creates VIDEO-mode landmarkers, accepts frames with strictly increasing timestamps, and closes them on exit (a second `process` after exit raises). A real face cannot be generated at test time, so face/hand output mapping is covered by 6.
6. Mapping from MediaPipe result objects to `FaceSample`/`HandSample`/`PoseSample` is a pure function tested with fake result objects: blendshape names/scores, world vs image landmarks, handedness label and score, pose visibility; hands sorted by score descending.
7. `MediaPipeAnalyzer.analyze` with a fake session: calls `process` once per decoded frame, calls `ctx.check()` per frame, closes the session on success, on `JobCancelled`, on deadline and on error.
8. Pose landmarker is not created when `USE_POSE` is false (`pose is None` on every frame).
9. `decide()` returns `false/false` and stats with `frames`, `face_frames`, `hand_frames` counts; the job log line contains them and no landmark values.
10. `create_app()` without an injected analyzer uses `MediaPipeAnalyzer`.
11. If the process-pool path is chosen: cancellation and deadline still stop a running job (criteria 12-13 of Phase 1 still pass against it).
12. README documents fetching models (`uv run python -m devtools.fetch_models`) and the benchmark tool; `.gitignore` has `models/`.

**Files.** Create: `video/models.lock`, `video/app/landmarks.py`, `video/app/decision.py`, `video/devtools/__init__.py`, `video/devtools/fetch_models.py`, `video/devtools/benchmark_frames.py`. Modify: `video/app/analyzer.py`, `video/app/main.py`, `video/app/jobs.py` (only if processes are chosen), `video/app/config.py`, `video/pyproject.toml`, `video/uv.lock`, `video/.env.example`, `video/.gitignore`, `video/README.md`, `video/CLAUDE.md`, this file.

**Out of scope.** The decision logic (Phases 5, 6), Docker (Phase 4), GPU delegate.

**Open questions.**
- mediapipe version: 1.1.1 (older, ≥ 2 weeks) or 1.2.0 (newest; "run face blendshapes on CPU with the GPU delegate" fix irrelevant on CPU)? Recommendation: the newest version that is ≥ 2 weeks old when the phase starts.
- Threads or processes? Recommendation: decide by `benchmark_frames.py --parallel 2/4`; default to threads (simpler cancellation) unless they do not scale.
- Model files in the repo or downloaded? Recommendation: downloaded and hash-checked (`models.lock`), never committed; baked into the image in Phase 4.
- Pose in the bundle validation even when `USE_POSE=false`? Recommendation: no; load only what is used.

**Done when.** The criteria pass; `benchmark_frames.py` numbers (ms/frame per model, threads vs processes, on the developer machine) are recorded in Decisions.

**Decisions.**
- `mediapipe==1.1.1` (1.2.0 was under 2 weeks old). Tasks import path in 1.x: `from mediapipe.tasks.python import vision` (`mp.tasks.vision` also works); `mp.Image`/`mp.ImageFormat.SRGB` at the top level. It pulls in `opencv-contrib-python` (needs GL libraries in Docker, Phase 4).
- Models: float16 version-1 URLs in `models.lock` (identical to `latest` at the time). `models.lock` is read from the project root next to `app/`; the hashes are re-checked on every `load()`.
- `PlaceholderAnalyzer` deleted (nothing used it); the test that relied on the default analyzer answering `false/false` was removed, since `create_app()` now defaults to `MediaPipeAnalyzer`.
- `MediaPipeAnalyzer` takes an optional `session_factory` (test seam); `ModelBundle.validate()` builds one session with `num_hands=1`.
- Handedness is stored as MediaPipe reports it (MediaPipe assumes a mirrored image); Phase 6 must account for that.
- Benchmark (developer machine, 12 logical CPUs, Windows, 20 s 1280x720 `testsrc` clip at 640 px / 5 fps = 100 frames; no face or hands in it, so the numbers understate the load from real recordings): face 1.8 ms/frame, hands 10.8 ms/frame, pose 23.7 ms/frame, job 2.0 s (6.4 s with pose). 2 jobs: threads 4.3 s vs processes 7.3 s wall; 4 jobs: threads 4.4 s vs processes 6.9 s. Threads scale (MediaPipe releases the GIL), so `asyncio.to_thread` stays and `jobs.py` is unchanged. Rerun on a real clip in Phase 7.

---

## Phase 4 — Packaging and compose

**Goal.** The real video lane runs in Docker and replaces the fake video worker in `compose.yaml`.

**Build.**
- `video/Dockerfile`: `python:3.13-slim-bookworm`; uv from `ghcr.io/astral-sh/uv:0.9.5` (same pin as server); apt `ffmpeg` plus the shared libraries mediapipe/OpenCV need (`libgl1`, `libglib2.0-0`, and anything the image test shows missing), `--no-install-recommends`, apt lists removed; `uv sync --frozen --no-dev`; models fetched at build time with `devtools/fetch_models.py` into `/models` (hash-checked; build fails on mismatch); non-root user uid 1000 (same uid as the server, so files on the scratch volume are readable); `MAI_VIDEO_MODEL_DIR=/models`, `MAI_VIDEO_ALLOWED_MEDIA_DIR=/scratch`; `EXPOSE 8002`; `HEALTHCHECK` on `http://127.0.0.1:8002/health/ready` with python `urllib` and a start period long enough for model loading; `CMD uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8002` (one worker, no reload).
- `video/.dockerignore`: `.env`, `.venv`, `tests`, `models`, `__pycache__`, `.pytest_cache`, `.ruff_cache`.
- `compose.yaml` `video-worker`: `build: ./video`, `image: readright-mai-video`, no `command` override, `scratch:/scratch:ro`, healthcheck on `/health/ready`, `restart: unless-stopped`, a CPU limit (`cpus`) from `${MAI_VIDEO_CPUS:-4}`. `audio-worker` keeps the fake from the api image.
- Server test change (needs the user's explicit OK; see open questions): `server/tests/test_compose_stack.py::test_fake_workers_run_from_the_api_image` drops the `video-worker` case; the video-worker checks move to a new video test.
- Root `.env.example`: `MAI_VIDEO_CPUS`.
- Docs: `video/README.md` "Run with Docker", `../README.md` current state, `../docs/deployment-architecture.md` (video worker container: MediaPipe on CPU, models baked into the image, CPU limit), `server/README.md` "Fake workers" (only audio is fake now).

**Acceptance criteria.**
1. Dockerfile: base `python:3.13-slim-bookworm`; ffmpeg via apt `--no-install-recommends` with lists removed; uv from a pinned image; `uv sync --frozen --no-dev`; `tests/` not copied.
2. Models are fetched at build time through `fetch_models.py` into `/models`; `MAI_VIDEO_MODEL_DIR=/models`.
3. Non-root `USER` (uid 1000); `MAI_VIDEO_ALLOWED_MEDIA_DIR=/scratch`; `EXPOSE 8002`; `CMD` runs `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8002` without `--workers`/`--reload`.
4. `HEALTHCHECK` hits `http://127.0.0.1:8002/health/ready` with `urllib`, has a `--start-period`.
5. `.dockerignore` excludes the listed entries.
6. `docker build video/` succeeds and `docker run` of the image reaches `/health/ready` 200 (skipped without docker).
7. Inside the built image `python -c "import mediapipe"` and an analysis of a generated `testsrc` webm succeed (skipped without docker) — this catches missing system libraries.
8. `compose.yaml` `video-worker`: builds `./video`, own image name, no fake command, `scratch` mounted read-only at `/scratch`, `/health/ready` healthcheck, `restart: unless-stopped`, a `cpus` limit; `api` still `depends_on` it; `docker compose config` validates with and without the override (skipped without docker).
9. The server's compose tests pass after the agreed change; no other server test changes.
10. With the stack up locally, the server smoke test (`READRIGHT_SMOKE_URL`) still passes with the real video lane.

**Files.** Create: `video/Dockerfile`, `video/.dockerignore`. Modify: `compose.yaml`, `.env.example` (root), `server/tests/test_compose_stack.py` (only the approved change), `server/README.md`, `../README.md`, `../docs/deployment-architecture.md`, `video/README.md`, `video/CLAUDE.md`, this file.

**Out of scope.** The real audio lane, GPU runtime config, ECR/CI, changes to `server/app/`.

**Open questions.**
- The existing server test pins `video-worker` to the fake. Options: (a) edit that one server test (recommended, smallest change, needs your explicit OK because the phase workflow forbids editing existing tests), (b) keep the fake in `compose.yaml` and add the real lane in a separate `compose.video.yaml`, (c) keep both services with a compose profile. Recommendation: (a).
- CPU limit for the video worker: Recommendation: `cpus: 4` of 8 by default (`MAI_VIDEO_CPUS`), tuned in Phase 7.
- Should the image run as the same uid as the server? Recommendation: yes (1000), so scratch files written by the api are readable.

**Done when.** `docker compose up --build` serves `POST /v1/analyses` end to end with the real video lane and the fake audio lane.

**Decisions.** (filled in when the phase is done)

---

## Phase 5 — Lack of expression

**Goal.** `is_lack_expression` is true when the reader's face shows little or no change of expression over the recording, judged from MediaPipe face blendshapes, ignoring the movements that reading itself causes.

**Algorithm.**
1. Valid frames: frames with `face is not None`. `coverage = valid / total`.
2. Not enough face: `total == 0`, `valid < EXPRESSION_MIN_FACE_FRAMES` or `coverage < EXPRESSION_MIN_FACE_COVERAGE` → `is_lack_expression = False`, `reason = "insufficient_face"` (we do not flag a child on missing data).
3. Excluded blendshapes (moved by speech, blinking and reading eye movement): `_neutral`, every `jaw*`, `mouthClose`, `mouthFunnel`, `mouthPucker`, `mouthLeft`, `mouthRight`, `mouthRollLower`, `mouthRollUpper`, `mouthShrugLower`, `mouthShrugUpper`, `mouthLowerDown*`, `mouthUpperUp*`, `mouthStretch*`, `cheekPuff`, `eyeBlink*`, `eyeLook*`.
4. Groups; each group's per-frame value is the mean of its members present in the frame:
   | Group | Members |
   |---|---|
   | `brow_raise` | `browInnerUp`, `browOuterUpLeft`, `browOuterUpRight` |
   | `brow_down` | `browDownLeft`, `browDownRight` |
   | `smile` | `mouthSmileLeft`, `mouthSmileRight`, `mouthDimpleLeft`, `mouthDimpleRight` |
   | `frown` | `mouthFrownLeft`, `mouthFrownRight`, `mouthPressLeft`, `mouthPressRight` |
   | `squint` | `eyeSquintLeft`, `eyeSquintRight`, `cheekSquintLeft`, `cheekSquintRight` |
   | `eye_wide` | `eyeWideLeft`, `eyeWideRight` |
   | `sneer` | `noseSneerLeft`, `noseSneerRight` |
5. Smooth each group series (valid frames in order) with a centered rolling median of `EXPRESSION_SMOOTH_FRAMES` (odd; edges use the available window).
6. Variability per group = `percentile(95) − percentile(5)` (numpy, linear) of the smoothed series. This is relative to the child's own resting face.
7. Threshold per group = `EXPRESSION_GROUP_MIN_RANGE.get(group, EXPRESSION_MIN_RANGE)`.
8. `is_lack_expression = all(variability[g] < threshold[g])`; `reason = "flat"` or `"expressive"`.
9. Stats added to `Decision.stats`: `face_coverage`, `face_frames`, `expression_reason`, `expr_var_<group>` for each group. No per-frame values are logged.

**Build.** `app/expression.py`: `ExpressionResult(is_lack_expression: bool, reason: str, coverage: float, face_frames: int, variability: dict[str, float])`, `assess_expression(features: list[FrameFeatures], settings) -> ExpressionResult`, plus the group table and excluded-name list as module constants. `decide()` uses it for `is_lack_expression`. README section "How the expression check works".

**Settings.** `MAI_VIDEO_EXPRESSION_MIN_FACE_COVERAGE` (0.5, 0-1), `MAI_VIDEO_EXPRESSION_MIN_FACE_FRAMES` (20, ≥ 1), `MAI_VIDEO_EXPRESSION_SMOOTH_FRAMES` (3, odd, ≥ 1), `MAI_VIDEO_EXPRESSION_MIN_RANGE` (0.12, 0-1), `MAI_VIDEO_EXPRESSION_GROUP_MIN_RANGE` (JSON object of group → value, default `{}`; unknown group names rejected at startup). Starting values; Phase 7 calibrates them.

**Acceptance criteria.** (all with synthetic `FrameFeatures`, no MediaPipe)
1. Constant blendshapes on every frame → `is_lack_expression = True`, `reason = "flat"`.
2. A `smile` that rises from 0.05 to 0.6 and back over part of the recording → `False`, `reason = "expressive"`.
3. Large changes only in excluded blendshapes (`jawOpen`, `mouthClose`, `eyeBlinkLeft`, `eyeLookInRight`, ...) with other groups constant → `True`.
4. A single-frame spike in one group (jitter) with `SMOOTH_FRAMES=3` → `True` (removed by the median).
5. Coverage below `MIN_FACE_COVERAGE`, or fewer than `MIN_FACE_FRAMES` valid frames, or zero frames → `False`, `reason = "insufficient_face"`.
6. Each group fires on its own: for every group, a change of `threshold + 0.05` in its members only → `False`; a change of `threshold − 0.05` → `True`.
7. `EXPRESSION_GROUP_MIN_RANGE={"smile": 0.5}` raises only the smile threshold.
8. A group's value is the mean of the members present; a frame missing some members still works.
9. `decide()` returns `is_lack_expression` from `assess_expression` and adds the listed stats keys; `is_finger_pointing` is still `False`.
10. Settings validation: even `SMOOTH_FRAMES`, coverage outside 0-1, unknown group name in the JSON → startup error.

**Files.** Create: `video/app/expression.py`. Modify: `video/app/decision.py`, `video/app/config.py`, `video/.env.example`, `video/README.md`, `video/CLAUDE.md`, this file.

**Out of scope.** Pointing, calibration on real recordings, head movement.

**Open questions.**
- Missing face data: `False` or an error? Recommendation: `False` + `reason` in the log; an error would fail the whole analysis for the student.
- Does head movement (nodding, tilting) count as expression? Recommendation: not in v1; the requirement is facial expression. Revisit after Phase 7 if flat readers who move their head a lot are being flagged.
- One global threshold or per group? Recommendation: global default plus optional per-group overrides, so Phase 7 can tune groups without code changes.

**Done when.** The criteria pass and the README explains the check in plain words.

**Decisions.** (filled in when the phase is done)

---

## Phase 6 — Finger pointing

**Goal.** `is_finger_pointing` is true when a pointing hand is seen for long enough, or when the reader's hand moves like it is tracking lines of text; optionally, when the arm reaches toward the screen with the hand out of view.

**Algorithm.** Hand landmark indices: wrist 0; thumb 1-4; index MCP 5, PIP 6, DIP 7, TIP 8; middle 9-12; ring 13-16; pinky 17-20. Pose indices: shoulders 11/12, elbows 13/14, wrists 15/16.

*Cue A — pointing hand shape (per frame), using `HandSample.world` (3D, so it works whichever way the hand faces the camera):*
- `bend(J)` = angle in degrees between `J − prev(J)` and `next(J) − J` (0 = straight).
- Finger extended: `bend(PIP) < POINTING_EXTENDED_MAX_BEND_DEG` and `bend(DIP) < POINTING_EXTENDED_MAX_BEND_DEG` and `|tip − wrist| > |pip − wrist|`.
- Finger curled: `bend(PIP) ≥ POINTING_CURLED_MIN_BEND_DEG` or `|tip − wrist| < |mcp − wrist|`.
- Pointing hand: `score ≥ POINTING_MIN_HAND_SCORE`, index extended, at least 2 of middle/ring/pinky curled. The thumb is ignored.
- Frame evidence A: any hand in the frame is a pointing hand.

*Cue C — arm toward the screen, hand not seen (per frame, only when `USE_POSE`):* no hand detected, and for the left or right side: wrist visibility ≥ `POINTING_POSE_MIN_VISIBILITY`, wrist `y ≥ 1 − POINTING_POSE_BOTTOM_BAND` (near the bottom edge), wrist `x` between the shoulders' `x` widened by 0.1 on each side, and elbow `y >` wrist `y` (forearm raised).

*Episodes:* frame evidence = A or C. Consecutive evidence frames form runs; runs separated by a gap ≤ `POINTING_MERGE_GAP_MS` merge; an episode lasts `last_ts − first_ts + frame_interval`; episodes shorter than `POINTING_MIN_EPISODE_MS` are dropped. Shape/arm decision: total episode time ≥ `max(POINTING_MIN_TOTAL_MS, POINTING_MIN_TOTAL_FRACTION × recording_ms)`, where `recording_ms = frames × frame_interval`.

*Cue B — text tracking motion (when `POINTING_USE_SWEEPS`):* per frame, the x (image, normalized) of the index fingertip of the highest-score hand; frames without a hand split the track into segments. Each segment is smoothed with a 3-frame median. A sweep is a stretch where x moves in one direction (reversals ≤ 0.01 tolerated) for ≥ `SWEEP_MIN_MS` with net movement ≥ `SWEEP_MIN_DX`, followed within `SWEEP_RETURN_MAX_MS` by movement the other way of ≥ half that distance (the line break). Either direction counts (cameras may mirror). Sweeps ≥ `SWEEP_MIN_COUNT` → pointing.

*Result:* `is_finger_pointing = shape/arm decision or sweep decision`. No hand and no pose evidence at all → `False`, `reason = "no_hands"`. Stats added: `pointing_frames`, `pointing_episodes`, `pointing_total_ms`, `sweeps`, `pose_frames`, `pointing_reason` (`"shape"`, `"sweeps"`, `"both"`, `"none"`, `"no_hands"`).

**Build.** `app/pointing.py`: `bend_angles(world) -> dict`, `is_pointing_hand(hand, settings) -> bool`, `arm_toward_screen(pose, settings) -> bool`, `episodes(evidence: list[bool], timestamps_ms: list[int], interval_ms: float, settings) -> list[tuple[int, int]]`, `count_sweeps(xs: list[float | None], timestamps_ms, settings) -> int`, `assess_pointing(features, plan, settings) -> PointingResult(is_finger_pointing, reason, stats)`. `decide()` uses it. README section "How the finger-pointing check works", including the known false-positive sources below.

**Settings.** `MAI_VIDEO_POINTING_MIN_HAND_SCORE` (0.5), `_EXTENDED_MAX_BEND_DEG` (35), `_CURLED_MIN_BEND_DEG` (60), `_MERGE_GAP_MS` (400), `_MIN_EPISODE_MS` (600), `_MIN_TOTAL_MS` (2000), `_MIN_TOTAL_FRACTION` (0.05), `_USE_SWEEPS` (true), `MAI_VIDEO_SWEEP_MIN_MS` (1000), `MAI_VIDEO_SWEEP_MIN_DX` (0.10), `MAI_VIDEO_SWEEP_RETURN_MAX_MS` (1000), `MAI_VIDEO_SWEEP_MIN_COUNT` (2), `MAI_VIDEO_POINTING_POSE_MIN_VISIBILITY` (0.5), `MAI_VIDEO_POINTING_POSE_BOTTOM_BAND` (0.3). Pose cue uses the existing `MAI_VIDEO_USE_POSE` (default false). All starting values; Phase 7 calibrates them.

**Acceptance criteria.** (synthetic landmarks, no MediaPipe; test helpers build world landmarks for a pointing hand, a fist, an open hand, a "tablet grip" hand)
1. `bend_angles` gives ~0° for a straight finger and ~90° for a right-angle bend.
2. Pointing hand → true; fist, open hand and tablet grip → false; pointing hand with `score` below the minimum → false.
3. The pointing test gives the same answer after rotating the world landmarks by an arbitrary 3D rotation (viewpoint independence).
4. `episodes`: gaps ≤ merge gap merge; shorter-than-minimum episodes drop; durations as defined.
5. Pointing hand in 3 s of a 30 s recording at 5 fps → true; in 1 s → false; minimum total scales with `MIN_TOTAL_FRACTION` on a long recording.
6. Isolated single-frame pointing detections scattered through the recording (no episode ≥ minimum) → false.
7. `count_sweeps`: a synthetic sawtooth (slow left-to-right over 2 s, fast return, repeated 3 times) → 3; the mirrored sawtooth → 3; jitter around a fixed point → 0; one slow drift with no return → 0; a sweep interrupted by frames without a hand is not counted across the gap.
8. Two sweeps with no pointing shape → `is_finger_pointing = True`, `reason = "sweeps"`; with `POINTING_USE_SWEEPS=false` → `False`.
9. Pose cue: with `USE_POSE=false` pose never counts; with it on, an arm-toward-screen pose for 3 s with no hand → true; the same pose while a non-pointing hand is detected → does not count.
10. No hands and no pose evidence → `False`, `reason = "no_hands"`.
11. `decide()` returns both flags (expression from Phase 5, pointing from here) and the listed stats keys.
12. Settings validated (angles 0-180, fractions 0-1, positive durations, `SWEEP_MIN_COUNT ≥ 1`) and documented.

**Files.** Create: `video/app/pointing.py`. Modify: `video/app/decision.py`, `video/app/config.py`, `video/.env.example`, `video/README.md`, `video/CLAUDE.md`, this file.

**Out of scope.** Training a classifier; calibrating on real recordings (Phase 7).

**Open questions.**
- Recording device: laptop webcam, tablet/phone front camera, or both? A tablet camera barely sees a finger on the glass; a laptop camera often sees only the hand rising from the bottom edge. Recommendation: confirm with the FE team before Phase 7 data collection; the rules above cover both, the thresholds will differ.
- Pose cue C in v1? Recommendation: build it behind `MAI_VIDEO_USE_POSE=false`; enable it only if Phase 7 shows it catches pointing that A and B miss without extra false positives.
- Known false positives: a hand resting on the chin, hands holding a tablet at the frame edges, a parent's hand. Recommendation: A requires the pointing shape and a minimum duration; measure the rest in Phase 7.
- Should cue B require some hand-shape support? Recommendation: no in v1 (partial or foreshortened hands are exactly what B is for); revisit after calibration.

**Done when.** The criteria pass and the README explains the check and its limits.

**Decisions.** (filled in when the phase is done)

---

## Phase 7 — Calibration, benchmark and docs

**Goal.** Thresholds chosen from real, labeled recordings; the lane proven to fit the time budget alongside the audio lane; docs final.

**Build.**
- `devtools/extract_features.py`: runs probe → frames → landmarks on each webm in a folder and saves the `FrameFeatures` series per file to a local cache directory (`.npz`, outside the repo or git-ignored; landmarks only, never frames).
- `devtools/calibrate.py`: reads a labels CSV (`file,is_finger_pointing,is_lack_expression`, extra columns ignored) and the feature cache; runs `assess_expression` / `assess_pointing` over a grid of values for the main thresholds (`EXPRESSION_MIN_RANGE`, `POINTING_MIN_TOTAL_MS`, `POINTING_EXTENDED_MAX_BEND_DEG`, `SWEEP_MIN_COUNT`, `USE_POSE` on/off); prints precision, recall, F1 and the confusion counts per flag per value, and the files misclassified at the recommended value. No network, no writes besides its report.
- `devtools/inspect_video.py`: one webm → per-frame summary (face found, group values, hands, pointing evidence, sweeps) as CSV to stdout and the final decision; optional `--overlay out.mp4` draws landmarks for local debugging (consented clips only; never in the image).
- `devtools/load_test.py`: sends N concurrent jobs to a running lane URL (files must already be on its scratch volume) and prints p50/p95/max job time and `/stats`.
- Update defaults in `Settings`, `.env.example` and the README table to the calibrated values.
- Docs: `video/README.md` final (current state, calibration workflow, accuracy on the eval set without any identifying details); `../docs/deployment-architecture.md` (video models chosen, measured per-recording time, time budget row, the "Which open-source video models" open decision closed); `../README.md` current state; `video/CLAUDE.md` devtools layout.

**Acceptance criteria.**
1. `calibrate.py` with a synthetic feature cache and labels CSV (made in the test) prints, for each flag and threshold value, precision, recall, F1 and TP/FP/TN/FN that match hand-computed numbers.
2. `calibrate.py` reports labeled files missing from the cache (and cache files without labels) instead of crashing.
3. `extract_features.py` writes one cache file per input with timestamps and the face/hand/pose arrays, and never writes image data (tested with a fake session).
4. `inspect_video.py` without `--overlay` writes only CSV to stdout (tested with a fake session).
5. `load_test.py` computes p50/p95/max correctly from fake responses (respx or a local ASGI app).
6. Defaults in `Settings`, `.env.example` and the README table agree (existing consistency test still passes).
7. Manual (recorded in Decisions, not automated): calibration run on the labeled set with the chosen values and their precision/recall; load test of 10 concurrent jobs on the g5.2xlarge together with the audio lane, showing the video lane's p95 inside the 75 s analysis window; the chosen `MAI_VIDEO_MAX_CONCURRENT_JOBS`, `MAI_VIDEO_SAMPLE_FPS` and `MAI_VIDEO_CPUS`.

**Files.** Create: `video/devtools/extract_features.py`, `video/devtools/calibrate.py`, `video/devtools/inspect_video.py`, `video/devtools/load_test.py`. Modify: `video/app/config.py` (defaults only), `video/.env.example`, `video/.gitignore` (feature cache), `video/README.md`, `video/CLAUDE.md`, `../README.md`, `../docs/deployment-architecture.md`, `compose.yaml` (only the CPU default if changed), this file.

**Out of scope.** Training a learned classifier (listed under "Later"), contract changes.

**Open questions.**
- Labeled recordings: can the team record ~30-50 consented clips on the target device (pointing / not pointing, expressive / flat, and hard cases: chin on hand, tablet grip, poor light)? Recommendation: yes; without them the thresholds are guesses. Clips and labels stay outside the repo.
- Which error to minimize? Recommendation: favor precision on both flags (a false flag on a child is worse than a miss); pick the value with precision ≥ 0.9 and the best recall.
- If the video lane does not fit the budget: Recommendation, in order: lower `SAMPLE_FPS`, lower `MAX_FRAMES`, drop pose, smaller `FRAME_WIDTH`, then more CPUs for the lane.

**Done when.** Calibrated defaults are in place with their measured precision/recall in Decisions, the load test fits the budget, and the root docs describe the real video lane.

**Decisions.** (filled in when the phase is done)

---

## Later (not planned as a phase)

- A learned pointing classifier (MediaPipe Model Maker gesture recognizer, or a small model over windows of landmark features) once there are enough labeled clips.
- Head movement as part of expression, if calibration shows it matters.
- More video checks ("More checks may be added later" in the contract); each needs a contract change first.
