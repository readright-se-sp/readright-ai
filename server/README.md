# ReadRight MAI server

The HTTP entry point of the MAI. It implements the BE ⇄ MAI contract in [`../contracts/be-mai-openapi.yaml`](../contracts/be-mai-openapi.yaml) (v1.1.1). See the [root README](../README.md) for how the whole system works.

> **Current state (Phase 1):** `POST /v1/analyses` checks the API key, validates the request and returns **stub data**, marked with the response header `X-MAI-Stub: true`. It does not download the video, call the analysis lanes or touch S3 yet. No Redis, ffmpeg, AWS credentials or Docker are needed to run it.

## Prerequisites

- **Python 3.11 or newer.** Tested on 3.11 and 3.13.
- **[uv](https://docs.astral.sh/uv/)** (recommended), or plain `pip`.
  - Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Setup

All commands run from this `server/` folder.

### 1. Install dependencies

With uv (installs the exact versions in `uv.lock`, including dev tools):

```bash
uv sync --extra dev
```

Or with pip:

```bash
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# macOS / Linux:        source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Create your `.env`

```bash
cp .env.example .env            # macOS / Linux / Git Bash
Copy-Item .env.example .env     # PowerShell
```

Then set `MAI_API_KEY` in `.env`. Any non-empty string works locally, but `change-me` (the placeholder from `.env.example`) and empty keys are rejected at startup. To generate a strong one, use either:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
openssl rand -base64 32
```

`.env` is read from the **current folder**, so start the server from `server/`. It is git-ignored; never commit it.

## Configuration

Settings come from environment variables with the `MAI_` prefix, or from `.env`. Real environment variables take priority over `.env`. At startup the server logs a warning naming any setting overridden this way (names only, never values), and an `api key loaded` line whose `source` is `environment` or `.env`.

| Variable | Default | Description |
|---|---|---|
| `MAI_API_KEY` | **required** | Key the BE sends in `X-API-Key`. The server won't start without it. |
| `MAI_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `MAI_REQUEST_DEADLINE_SECONDS` | `115` | Internal deadline per request (contract limit is 120 s). Used from Phase 3. |
| `MAI_ALLOW_AUDIO_ONLY_ON_VIDEO_FAILURE` | `false` | If the video lane fails: `false` fails the request, `true` returns an audio-only result. Used from Phase 4. |
| `MAI_DEFAULT_OUTPUT_KEY_PREFIX` | `analyses/{request_id}/miscues/` | S3 prefix for mini audio files when the request has no `output_key_prefix`. |

## Run

```bash
uv run uvicorn app.main:create_app --factory --reload --port 8000
```

Without uv, activate the venv and drop `uv run`. `--reload` restarts the server when code changes; leave it out outside development.

Then open:

- **http://localhost:8000/docs** — interactive API docs. Click **Authorize**, enter your `MAI_API_KEY`, and use **Try it out** on `POST /v1/analyses`.
- **http://localhost:8000/health/ready** — readiness check (no key needed).

### Try it from the command line

Replace `dev-key` with your `MAI_API_KEY`.

macOS / Linux / Git Bash:

```bash
curl -i -X POST http://localhost:8000/v1/analyses \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key" \
  -d '{
    "request_id": "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11",
    "video_url": "https://bucket.s3.ap-southeast-1.amazonaws.com/recordings/abc.webm?X-Amz-Signature=x",
    "passage_title": "Pam'\''s Cat",
    "passage_content": "Pam has a cat. It is on the bed."
  }'
```

PowerShell:

```powershell
$body = @{
  request_id      = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"
  video_url       = "https://bucket.s3.ap-southeast-1.amazonaws.com/recordings/abc.webm?X-Amz-Signature=x"
  passage_title   = "Pam's Cat"
  passage_content = "Pam has a cat. It is on the bed."
} | ConvertTo-Json

Invoke-RestMethod http://localhost:8000/v1/analyses -Method Post -ContentType "application/json" `
  -Headers @{ "X-API-Key" = "dev-key" } -Body $body | ConvertTo-Json -Depth 5
```

`Invoke-RestMethod` throws on 4xx/5xx; use `curl.exe -i ...` in PowerShell to see error bodies.

What to expect:

| Try | Result |
|---|---|
| Valid request with the key | `200`, stub analysis, header `X-MAI-Stub: true` |
| No key or wrong key | `401` |
| Missing field, bad UUID, empty passage, malformed JSON | `400 INVALID_REQUEST` |
| Add `"output_key_prefix": "custom/path"` | Mini audio keys become `custom/path/miscue-0001.wav`, … |

Every error body has the contract's shape: `{"error": {"code": ..., "message": ..., "details": ...}}`.

## Test and lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .     # `uv run ruff format .` to fix
```

Without uv, activate the venv and drop `uv run`. Tests don't need a `.env` and ignore any `.env` file or `MAI_*` variables you have set.

`tests/test_contract.py` checks the models and responses against `../contracts/be-mai-openapi.yaml`. If you change the contract, those tests tell you what the server must change to match.

## Project layout

```
server/
├── app/
│   ├── main.py          # create_app(): app factory, middleware, routers
│   ├── config.py        # Settings (MAI_* env vars / .env)
│   ├── models.py        # contract request/response/error models
│   ├── errors.py        # MAIError, error code → HTTP status, error handlers
│   ├── auth.py          # X-API-Key middleware (checked before the body is parsed)
│   ├── logging.py       # JSON logs with request_id
│   ├── stub.py          # dummy analysis (replaced in Phase 4)
│   └── routes/          # analyses.py, health.py
├── tests/
├── .env.example
├── pyproject.toml
└── uv.lock
```

## Troubleshooting

- **`1 validation error for Settings` / `api_key` / `Field required` on startup.** `MAI_API_KEY` is not set. Create `.env` (see Setup) and start the server from `server/`, or set the variable in your shell.
- **The key in `.env` is ignored / an old key still works.** A `MAI_API_KEY` variable in your shell overrides `.env`. Look for the `environment variable overrides .env` warning (and `"source": "environment"` in the `api key loaded` line) in the startup log. Remove it with `Remove-Item Env:MAI_API_KEY` (PowerShell) or `unset MAI_API_KEY` (bash), or open a new terminal, then restart the server.
- **Requests return 401 though the key is right.** The `X-API-Key` header is missing, empty or doesn't match `MAI_API_KEY`. Check for typos and stray spaces.
- **`MAI_API_KEY must not be empty` or `MAI_API_KEY is still the placeholder` on startup.** The key is empty or still `change-me`. Set a real key in `.env` (see Setup), and check that no empty `MAI_API_KEY` is set in your shell.
- **`ModuleNotFoundError: app`.** Dependencies aren't installed in the active environment. Run `uv sync --extra dev`, or activate the venv and `pip install -e ".[dev]"`.
- **Port 8000 already in use.** Pass another port, e.g. `--port 8080`.
