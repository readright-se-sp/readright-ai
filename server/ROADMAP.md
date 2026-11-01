# Server roadmap

The server is built in 7 phases. Each phase below says what to build, the decisions already made, the questions still open, and when it is done. A new session should be able to draft a phase plan from its section plus the sources listed under "Before planning".

## Status

| # | Phase | Status |
|---|---|---|
| 1 | Setup, contract layer, stub endpoint | Done |
| 2 | Idempotency (Redis) | Done |
| 3 | Deadline, download, preprocessing | Done |
| 4 | Worker contract and orchestration | Not started |
| 5 | Mini audio files (cut and upload) | Not started |
| 6 | Admission control and readiness | Not started |
| 7 | Packaging and local stack | Not started |

## How to work a phase

1. **Before planning**, read: this file, [CLAUDE.md](CLAUDE.md), [../CLAUDE.md](../CLAUDE.md), [the contract](../contracts/be-mai-openapi.yaml), [the deployment doc](../docs/deployment-architecture.md) (request flow, time budget, concurrency), and the current code in `app/` and `tests/`.
2. **Draft a plan** for the user: files to add or change, new settings, new dependencies, behavior and error mapping, tests, done criteria, and the phase's open questions with a recommended answer for each. Do not resolve an open question silently.
3. **Wait** for the user's approval of the plan and an explicit go. Never start a phase, or the next one, unprompted.
4. **Implement**, then run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
5. **Finish**: mark the phase done in the status table above and in the root README's "Current state"; add new settings to `.env.example` and the configuration table in [README.md](README.md); update [README.md](README.md), [../README.md](../README.md) and [../docs/deployment-architecture.md](../docs/deployment-architecture.md) if behavior changed; record decisions made during the phase in its section below.

## Target request flow

Where each phase hooks into `POST /v1/analyses` once everything is built:

1. API key check (middleware) — Phase 1
2. Body validation → 400 — Phase 1
3. Idempotency lookup: done → stored 200; in progress → 409 — Phase 2
4. Admission control → 429 — Phase 6
5. Idempotency claim (mark in progress) — Phase 2
6. Download webm, probe, convert to WAV, all inside the 115 s deadline — Phase 3
7. Audio lane (WAV + passage) and video lane (webm) in parallel — Phase 4
8. Cut and upload mini audio files — Phase 5
9. Merge, store result, delete scratch files, respond 200 — Phases 2, 4
10. Deadline passed at any step → cancel work, record failure, 504 `TIMEOUT` — Phase 3

---

## Phase 1 — Setup, contract layer, stub endpoint (done)

What exists: `app/` package with `create_app()` factory and settings on `app.state.settings`; contract models in `models.py`; `MAIError` + `STATUS_BY_CODE` in `errors.py` with 400 for validation errors and `Retry-After` on 429/503; `ApiKeyMiddleware` checking `X-API-Key` before the body is parsed (empty key or header rejected; `change-me` placeholder rejected at startup); JSON logging with a `request_id` contextvar; `/health/live` and `/health/ready` (named checks in `app.state.readiness_checks`); `POST /v1/analyses` returning deterministic stub data from `stub.py` with `X-MAI-Stub: true`; contract tests in `tests/test_contract.py`.

Decisions: package is `app/` (no `src/`); 401 uses code `INVALID_REQUEST` (contract has no auth code; flagged to the user as a possible contract addition); real env vars override `.env` and the server logs a warning when that happens.

## Phase 2 — Idempotency (Redis) (done)

**Goal.** A `request_id` is processed at most once at a time, and a finished result is returned again without reprocessing or re-uploading.

**Build.**
- Async Redis client (`redis.asyncio`) created in the app lifespan, stored on `app.state`, closed on shutdown; injectable so tests use `fakeredis`.
- One record per `request_id` (e.g. key `mai:req:<request_id>`) holding the state (`in_progress`, `done`), timestamps, and the stored response body when done.
- The claim must be atomic (`SET NX` or a small Lua script) so two simultaneous identical requests cannot both start.
- `done` → return the stored body as 200. `in_progress` → 409 `REQUEST_IN_PROGRESS`. Absent → claim and process.
- The `in_progress` record has a lease TTL longer than the 120 s SLA (e.g. 130 s), so a crashed request cannot block retries forever.
- On success store the result with a long TTL; on any error delete the record so the BE can retry with the same `request_id` (the contract says 500 and 504 are safe to retry).
- Redis unreachable during a request → 503 `SERVICE_UNAVAILABLE` with `Retry-After`. Add a `redis` readiness check (`PING`).
- Lookup happens before admission control (Phase 6) and claim after it, so a finished result is always returned even under load. Structure the code so Phase 6 can slot in between.
- Wrap the existing stub call; the stub result is what gets stored for now.

**Settings (proposed).** `MAI_REDIS_URL` (default `redis://localhost:6379/0`), `MAI_IN_PROGRESS_TTL_SECONDS` (130), `MAI_RESULT_TTL_SECONDS` (7 days).

**Tests.** fakeredis: first request processes and stores; repeat returns stored body without calling the processor again; concurrent duplicates (`asyncio.gather`) → exactly one 200 and the rest 409; lease expiry allows a retry; a failed request can be retried; Redis down → 503; readiness reports `redis`.

**Open questions.**
- Same `request_id` with a different body: return the stored result anyway, or reject? The contract is silent. Recommendation: store a hash of the body and reject a mismatch with 400 `INVALID_REQUEST` plus a clear message, and flag it to the user as a contract clarification.
- Should a stored replay carry a header (e.g. `X-MAI-Replayed: true`)? Helpful for debugging; not in the contract.
- Local development needs Redis: document `docker run -p 6379:6379 redis:7` (Windows: Docker Desktop or Memurai). No in-memory fallback, so dev matches production.

**Done when.** All of the above is tested, the README documents running Redis locally, and `.env.example` has the new settings.

Decisions:
- No `failed` state: any error (including cancellation) deletes the record, so errors are never cached and the BE can retry with the same `request_id`. Failures are visible in the logs.
- Claim, complete and release carry a random token; complete and release are Lua scripts that act only if the token still matches, so a request whose lease expired cannot overwrite or delete a newer claim.
- Same `request_id` with a different body → 400 `INVALID_REQUEST`. The fingerprint covers `passage_title`, `passage_content`, `output_key_prefix` (trailing slash ignored) and `video_url` without its query string, because the presigned signature changes on every re-sign. Flagged to the BE team as a contract clarification.
- Replays carry `X-MAI-Replayed: true` (header only, not in the contract).
- Redis error in lookup or claim → 503 `SERVICE_UNAVAILABLE`; in complete → still 200 (error logged, the lease expires); in release → logged, the original error is returned.
- Added `MAI_REDIS_TIMEOUT_SECONDS` (default 2) so a hung Redis cannot eat the deadline.
- No in-memory fallback: local development runs a real Redis; tests use fakeredis.
- The processor is `app.state.processor` (the stub for now); Phase 3/4 replace it. Phase 6 admission control slots in between `lookup` and `claim` in `routes/analyses.py`.

## Phase 3 — Deadline, download, preprocessing (done)

**Goal.** Every request runs against a 115 s deadline, and the server turns the presigned `video_url` into a local webm and a WAV ready for the lanes.

**Build.**
- A per-request deadline (`MAI_REQUEST_DEADLINE_SECONDS`, already in settings) started when the request arrives and available to every later step (e.g. a small `Deadline` object with `remaining()`), enforced with `asyncio.timeout` around the processing. On expiry: cancel in-flight work, release (delete) the idempotency record, return 504 `TIMEOUT`.
- Scratch directory per request under `MAI_SCRATCH_DIR`, always removed in `finally`.
- Download with a streaming `httpx.AsyncClient` to `<scratch>/<request_id>/input.webm`, with a byte limit and connect/read timeouts bounded by the deadline. Never log the URL's query string (signature); log the host only.
- Map failures: S3 403 for an expired signature → 422 `VIDEO_URL_EXPIRED`; other 4xx/5xx, network errors or size limit exceeded → 422 `VIDEO_DOWNLOAD_FAILED`.
- `ffprobe` the file: not webm/matroska, or no audio stream → 415 `UNSUPPORTED_MEDIA`.
- Convert with `ffmpeg` as an async subprocess: `-vn -ac 1 -ar 16000 -c:a pcm_s16le` → `audio.wav` (16 kHz, 16-bit PCM, mono — agreed). Kill the subprocess if the request is cancelled.
- Log the duration of each stage (download, probe, convert) to compare against the time budget in the deployment doc.

**Settings (proposed).** `MAI_SCRATCH_DIR` (default a temp dir), `MAI_MAX_VIDEO_BYTES` (e.g. 200 MB), `MAI_FFMPEG_PATH` / `MAI_FFPROBE_PATH` (default from `PATH`).

**Tests.** respx for downloads (success, expired 403, 404, 5xx, connection error, oversized); small webm fixtures in `tests/fixtures/` (with audio, without audio, not a webm) generated once with ffmpeg and committed; probe and convert produce a WAV with the right format (check with the `wave` module); deadline expiry → 504 and the scratch dir is gone; tests needing ffmpeg are skipped with a clear reason if it is not installed.

**Open questions.**
- A webm with audio but **no video** stream: 415, or continue audio-only? The contract only mentions a missing audio track. Recommendation: 415, since the video lane needs video.
- Once downloads are real, the stub can no longer be called with a fake URL. Keep a way to run the stub end to end for BE integration testing? Recommendation: a `MAI_PIPELINE_MODE=stub|real` setting (default `real`), removed after Phase 4 if no longer needed.
- How to detect an expired presigned URL reliably: S3 returns 403 with an XML body containing `Request has expired`; confirm with a real expired URL.

**Decisions.**
- A webm with audio but no video stream is rejected with 415 `UNSUPPORTED_MEDIA` (the video lane needs video).
- `MAI_PIPELINE_MODE=stub|real` (default `real`) keeps the stub runnable end to end; `stub` does no download.
- An expired presigned URL is an HTTP 403 whose body (first 4 KB) contains `Request has expired`. Not yet confirmed against a real expired S3 URL.
- ffmpeg is not required to run the tests: media fixtures are generated at test time (skipped if ffmpeg is missing) instead of being committed, and probe/convert unit tests monkeypatch `app.media.run_process`.

**Done when.** A real presigned URL (or a local test server) produces `input.webm` and `audio.wav` within the deadline; errors map as above; ffmpeg installation is documented for Windows (`winget install Gyan.FFmpeg`), macOS (`brew install ffmpeg`) and Linux.

## Phase 4 — Worker contract and orchestration

**Goal.** Define how the server talks to the audio and video lanes, call both in parallel, and build the real response. The stub is removed.

**Build.**
- **First** write `../contracts/worker-api.md` (with a version). Per lane:
  - `POST /v1/jobs` — request: `job_id` (the `request_id`), `deadline` (absolute UTC epoch ms), file paths on the shared scratch volume (audio: `wav_path`, `passage_title`, `passage_content`; video: `webm_path`). Response: the lane's result, or a typed error.
  - `GET /health/ready` — 200 when the models are loaded (used in Phase 6).
  - `GET /stats` — `queue_length`, recent average processing seconds (used in Phase 6).
  - Audio result: `miscues` (`actual_phrase`, `spoken_phrase`, `actual_phoneme`, `spoken_phoneme`, `start_ms`, `end_ms`, `miscue_type`), `reading_score`, `wpm`, and the audio flags `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous`, `is_lack_expression`, `is_hardly_audible`. Audio errors include no speech.
  - Video result: `is_finger_pointing` (more checks later).
  - Lanes never touch S3 and drop jobs whose deadline has passed.
- Worker clients (httpx) with timeouts from the request deadline; both lanes run with `asyncio` in parallel; on deadline, cancel both (close the connections; optionally a cancel endpoint).
- Error mapping: audio "no speech" → 422 `NO_SPEECH_DETECTED`; worker error or unreachable → 500 `PROCESSING_FAILED`; deadline → 504 `TIMEOUT`.
- Video failure policy (agreed): the whole request fails, unless `MAI_ALLOW_AUDIO_ONLY_ON_VIDEO_FAILURE=true`, in which case return the audio result with `is_finger_pointing=false` and log a warning.
- Merge into `AnalysisResponse`: convert lane times to the contract's milliseconds, assign `audio_object_key` values (`<prefix>miscue-0001.wav`…, prefix from `output_key_prefix` or `MAI_DEFAULT_OUTPUT_KEY_PREFIX`; reuse the prefix logic now in `stub.py`). The upload itself is Phase 5.
- Remove `stub.py`, the `X-MAI-Stub` header, and the Phase 3 stub mode if no longer needed.
- Provide **fake workers** (small FastAPI apps following the worker contract, e.g. `devtools/fake_workers.py`) so the server can run locally and in Phase 7's compose before the real lanes exist.

**Settings (proposed).** `MAI_AUDIO_WORKER_URL`, `MAI_VIDEO_WORKER_URL`, `MAI_WORKER_CONNECT_TIMEOUT_SECONDS`.

**Tests.** Fake workers via respx or in-process ASGI apps: both succeed → merged response validates against the contract; audio no-speech → 422; audio fails → 500; video fails → 500 by default and audio-only with the flag on; a slow worker → 504 and both calls cancelled; times converted correctly; keys numbered and prefixed correctly.

**Open questions.**
- Which flags come from which lane, and who computes `reading_score` and `wpm`? Recommendation: all audio flags, `reading_score` and `wpm` from the audio lane; `is_finger_pointing` from the video lane. Confirm with the user and the audio lane owner.
- Lane time unit (seconds as float, or ms as int)? Recommendation: ms ints, same as the contract.
- Ordering of miscues in the response: by `start_ms`.

**Done when.** `contracts/worker-api.md` exists and is approved; the server runs end to end against the fake workers; the stub is gone; README and docs describe the worker URLs.

## Phase 5 — Mini audio files (cut and upload)

**Goal.** For every miscue, cut its span out of the WAV and upload it to S3 under the key returned in `audio_object_key`.

**Build.**
- Cut each clip from `audio.wav` using the miscue's `start_ms`/`end_ms`. The WAV is PCM, so slicing frames with Python's `wave` module is exact and avoids an ffmpeg process per clip; output is WAV, 16 kHz, 16-bit PCM, mono (agreed).
- Upload with bounded concurrency (`MAI_UPLOAD_CONCURRENCY`), `ContentType: audio/wav`, to `MAI_S3_BUCKET` in `AWS_REGION`, credentials from the standard AWS chain (env, profile or instance role; never in `.env.example` values). Use `aioboto3`, or `boto3` in a thread pool.
- Upload happens inside the deadline and before the result is stored, so a replayed result always points to files that exist. Keys are deterministic, so a retry overwrites the same objects.
- Any upload failure → 500 `UPLOAD_FAILED`.

**Settings (proposed).** `MAI_S3_BUCKET`, `AWS_REGION` (default `ap-southeast-1`), `MAI_UPLOAD_CONCURRENCY` (e.g. 8), `MAI_CLIP_PADDING_MS`.

**Tests.** moto for S3: every miscue gets an object at its key with a valid WAV of the right length and format; padding is clamped to the recording's bounds; an upload error → 500 `UPLOAD_FAILED`; uploads respect the concurrency limit; no upload when there are no miscues.

**Open questions.**
- Padding around each clip so the word isn't cut off? Recommendation: ~150 ms each side, clamped, configurable.
- **Omissions** have no spoken audio, but the contract requires `audio_object_key`. Recommendation: the audio lane gives the time where the word should have been read, and the clip covers that gap; confirm with the user.
- Should old clips be cleaned up (S3 lifecycle rule)? Probably a bucket setting outside the server.

**Done when.** Clips are cut and uploaded in tests (moto) and once against the real bucket; IAM permissions needed (`s3:PutObject` on the prefix, `s3:ListBucket`/`HeadBucket` for readiness) are documented.

## Phase 6 — Admission control and readiness

**Goal.** Reject work the server cannot finish in time with an immediate 429, and report not-ready (503) until every dependency is up.

**Build.**
- Readiness checks in `app.state.readiness_checks`: `redis` (from Phase 2), `audio_worker` and `video_worker` (their `/health/ready`), `s3` (`HeadBucket`), optionally `ffmpeg` (binary present).
- Admission control: read each worker's `/stats` (cache for ~1 s), estimate when a new request would finish (download/preprocess estimate + the slower lane's queue × average time + upload estimate). If that is past the deadline → 429 `RATE_LIMITED` with `Retry-After` set from the estimated overshoot.
- A hard cap on requests in flight (`MAI_MAX_IN_FLIGHT`, e.g. 10) as a backstop → 429.
- Admission runs after the idempotency lookup and before the claim (see "Target request flow").
- Record the time of the last request so the instance's idle auto-stop script (30 minutes idle, see the deployment doc) can read it.

**Settings (proposed).** `MAI_MAX_IN_FLIGHT`, `MAI_STAGE_ESTIMATE_DOWNLOAD_SECONDS`, `MAI_STAGE_ESTIMATE_UPLOAD_SECONDS`, `MAI_STATS_CACHE_SECONDS`.

**Tests.** Estimator math; 429 with `Retry-After` when overloaded; cap enforced under concurrent requests; a finished `request_id` still returns its stored result when overloaded; readiness 503 lists which check failed; stats endpoint unreachable is treated as not ready.

**Open questions.**
- How should the idle script read the last-request time: a field in `/health/live`, a file on disk, or a Redis key? Recommendation: `last_request_at` in `/health/live`.
- Initial estimate values come from the benchmark in the deployment doc; use conservative defaults until then.
- In-flight cap with more than one uvicorn worker process: the cap is per process. Recommendation: run one uvicorn worker (the server only does async I/O).

**Done when.** 429 and 503 behave as above under test; defaults are documented with a note to tune them after the benchmark.

## Phase 7 — Packaging and local stack

**Goal.** Run the whole MAI with `docker compose up` locally and on the EC2 instance.

**Build.**
- `server/Dockerfile`: slim Python image matching the supported version, ffmpeg installed, dependencies from `uv.lock`, non-root user, one uvicorn worker, a `HEALTHCHECK` on `/health/live`.
- Root `compose.yaml` with `caddy`, `api`, `redis` (with a volume so stored results survive restarts), `audio-worker`, `video-worker` (the Phase 4 fakes until the real lanes exist), and a shared scratch volume mounted at the same path in `api` and both workers.
- Root `Caddyfile`: TLS for `readright-mai.reevsan.dev`, reverse proxy to `api`, upstream timeouts above 120 s.
- The API key comes from SSM Parameter Store / Secrets Manager as an environment variable at start, never baked into an image or committed.
- Root `.env.example` for compose-level settings; update both READMEs and the deployment doc.
- An end-to-end smoke test through compose (fake workers; S3 via MinIO or moto server locally).

**Open questions.**
- Local S3 for the smoke test: MinIO or moto server? Recommendation: MinIO.
- Whether the start/stop/idle-shutdown scripts belong in this repo (`scripts/`) or with the infrastructure.
- Python version for the image (3.11 minimum; 3.12 or 3.13 recommended).

**Done when.** `docker compose up --build` serves `POST /v1/analyses` end to end with fake workers locally; the README explains running the stack and deploying it to the instance.
