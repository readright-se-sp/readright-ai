# server/

FastAPI entry point of the MAI. Root rules in [../CLAUDE.md](../CLAUDE.md) also apply. Setup and config: [README.md](README.md). API: [../contracts/be-mai-openapi.yaml](../contracts/be-mai-openapi.yaml).

Work is done in phases: read [ROADMAP.md](ROADMAP.md) before planning or implementing anything, and follow its "How to work a phase" steps (plan → user approval → explicit go → implement → update status and docs).

## Commands

Run from `server/`. Python >= 3.11 (tested on 3.11 and 3.13).

- `uv sync --extra dev`
- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format --check .` (`uv run ruff format .` to fix)
- `uv run uvicorn app.main:create_app --factory --reload --port 8000`

Run pytest, ruff check and ruff format --check before calling work done.

## Layout

Package `app/` at the top level (no `src/` layout, intentionally).

- `main.py`: `create_app()` factory, middleware, routers.
- `config.py`: `Settings` (`MAI_*` env vars, `.env`).
- `models.py`: contract request/response/error models and enums.
- `errors.py`: `MAIError`, `STATUS_BY_CODE`, error handlers.
- `auth.py`: `ApiKeyMiddleware`.
- `logging.py`: JSON logs, `request_id_var`, request logging middleware.
- `idempotency.py`: `IdempotencyStore` (Redis claim / replay / release, `request_fingerprint`).
- `deadline.py`: `Deadline`, `deadline_var` (set by the route, read by the pipeline).
- `scratch.py`: `scratch_dir()` context manager (per-request dir, always removed).
- `download.py`: `download_video()` (streamed, size-limited, maps failures to `MAIError`; never logs the query string).
- `media.py`: `run_process()` (kills the child on cancel), `probe()`, `convert_to_wav()`.
- `workers.py`: lane clients `run_audio_lane` / `run_video_lane`, lane result models, lane error mapping.
- `merge.py`: `output_key_prefix()`, `merge_results()` (lane results to `AnalysisResponse`).
- `pipeline.py`: `pipeline_processor` (download, probe, convert, both lanes in parallel, merge).
- `devtools/fake_workers.py` (outside `app/`): fake audio/video lane apps for local runs.
- `routes/analyses.py`: `POST /v1/analyses`.
- `routes/health.py`: `/health/live`, `/health/ready`.

## Conventions

- The app is built by `create_app(settings=None)`. Settings live on `app.state.settings`. No global settings singleton.
- Errors: raise `MAIError(ErrorCode.X, message, details=..., retry_after=...)`. The status comes from `STATUS_BY_CODE`. Never return ad-hoc error JSON or raise a bare `HTTPException` for contract errors.
- Validation errors become 400 `INVALID_REQUEST` and never echo input (it may contain the presigned URL signature).
- Auth: `ApiKeyMiddleware` checks `X-API-Key` for `/v1/` paths before the body is parsed. Keep it in middleware.
- The app is built by `create_app(settings=None, redis=None, processor=None)`; tests inject a fakeredis client and a fake processor. A lifespan sets `app.state.idempotency` (the `IdempotencyStore`) and `app.state.processor` (`async (body, settings) -> AnalysisResponse`, `pipeline_processor` unless one is injected). It creates the Redis client only if none was injected and closes only its own.
- Idempotency: the route does lookup, claim, processor, then complete (release on any failure, including cancellation). Errors are never cached. Same `request_id` with a different body is 400. Phase 6 admission control goes between lookup and claim.
- Readiness: register dependency checks as named async `() -> bool` callables in `app.state.readiness_checks`. Any failure (or exception) returns 503 `SERVICE_UNAVAILABLE` with `Retry-After`.
- Settings use the `MAI_` prefix (pydantic-settings). Env vars override `.env` on purpose: production injects env vars.
- Every new setting must also be added to `.env.example` and the configuration table in [README.md](README.md).
- Logging: JSON via `app/logging.py`. Pass fields with `extra={...}`. `request_id` comes from the contextvar. Never log secrets or full URLs (host only).
- Use relative imports inside `app/`.
- Lane contract: `../contracts/worker-api.md`. Lane functions are called as module globals of `app.pipeline` so tests can monkeypatch them. Replays add `X-MAI-Replayed: true`.

## Testing

- Tests must not depend on a developer's `.env` or shell `MAI_*` vars. `conftest.py` isolates them; keep using the `settings`, `client` and `auth` fixtures and `valid_body()`.
- `tests/test_contract.py` must keep passing. It checks models, enums and version against the contract.
- Use fakes and mocks, not real Redis, S3, workers or network. Later phases plan fakeredis, respx and moto.
- Never put real keys in tests.
