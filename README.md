# readright-ai

The AI layer of **ReadRight** (the MAI, Microservices AI), an oral reading assessment platform based on the Phil-IRI. It takes a recording of a student reading a passage aloud and returns an analysis of the reading: a score, words per minute, reading-behavior flags, and a list of miscues with a short audio clip of each one.

This layer only processes media. It knows nothing about students, classes or assessments; those belong to the backend (BE).

```
Frontend  ⇄  BE (AWS Lambda)  ⇄  readright-ai (this repo)
```

## How it works

```
BE ──(request_id, presigned video_url, passage)──► server
                                                     ├─ downloads the webm from the presigned URL
                                                     ├─ preprocesses it into one WAV (16 kHz, 16-bit PCM, mono)
                                                     ├──► audio lane (WAV + passage) ──┐
                                                     └──► video lane (webm) ───────────┤   (run in parallel)
                                                     ◄── results ◄─────────────────────┘
                                                     ├─ cuts a mini audio file for each miscue from the WAV
                                                     ├─ uploads the mini audio files to S3
                                                     └─ merges everything into one response
BE ◄── analysis response (200, or a typed error)
```

1. The BE sends `POST /v1/analyses` with a presigned S3 GET URL of the recorded webm and the passage.
2. The server downloads the webm and converts its audio track into a single WAV file: 16 kHz sample rate, 16-bit PCM.
3. The WAV and the passage go to the **audio lane**; the webm goes to the **video lane**. Both run at the same time.
4. The audio lane returns the words and phrases that have miscues, each with the start and end time of where it was spoken.
5. For each miscue, the server cuts that span out of the WAV and uploads it to S3. These are the **mini audio files**. Their S3 keys are returned to the BE as `audio_object_key`.
6. The server merges the audio and video results into the response and returns it.

| Project | Role |
|---|---|
| `server/` | The only HTTP entry point. Checks the API key and idempotency, downloads and preprocesses the recording, sends work to both lanes, cuts and uploads the mini audio files, and merges the results. |
| `audio/` | Audio lane. Transcribes the WAV verbatim with CrisperWhisper, aligns it with the passage, and returns the miscues with their timestamps. |
| `video/` | Video lane. Analyzes the reader on video (for example, finger pointing). Models are not chosen yet. |

The audio and video lanes never touch S3, never call each other and never talk to the BE. They receive ready-to-use files from the server over a shared volume and return results. Only the server talks to S3.

## The API contract

The BE ⇄ MAI contract is [`contracts/be-mai-openapi.yaml`](contracts/be-mai-openapi.yaml). It is the source of truth; this section is a summary.

**Request:** `POST /v1/analyses` with an `X-API-Key` header.

| Field | Description |
|---|---|
| `request_id` | UUID from the BE. Idempotency key and correlation id. |
| `video_url` | Presigned S3 GET URL of the webm (audio and video). Must stay valid for the full 120 s. |
| `passage_title` | Title of the passage. |
| `passage_content` | Full text the student was asked to read. |
| `output_key_prefix` | Deprecated and ignored. Mini audio files always go under `analyses/<request_id>/miscues/`. |

**Response (200):** `reading_score`, `wpm`, the flags `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous`, `is_lack_expression`, `is_hardly_audible` and `is_finger_pointing`, and a `miscues` array. Each miscue has:

| Field | Description |
|---|---|
| `actual_phrase` / `spoken_phrase` | What the passage says and what was read. `spoken_phrase` is empty for an omission. |
| `actual_phoneme` / `spoken_phoneme` | Phonemes of each (e.g. IPA). `spoken_phoneme` is empty for an omission. |
| `read_start` / `read_end` | Where the miscue is in the recording, in **milliseconds**. |
| `audio_object_key` | S3 key of the mini audio file for this miscue. |
| `miscue_type` | One of `mispronunciation`, `omission`, `substitution`, `insertion`, `repetition`, `transposition`, `reversal`, `self-correction`. |

**Behavior the BE relies on:**

- **Synchronous, 120 s hard limit.** The server works to an internal 115 s deadline and returns `504 TIMEOUT` rather than holding the connection open.
- **Idempotent.** Repeating a finished `request_id` returns the stored result without reprocessing or re-uploading. Repeating one still in progress returns `409 REQUEST_IN_PROGRESS`.
- **Typed errors.** Every error body is `{"error": {"code", "message", "details"}}`:

| Status | Codes |
|---|---|
| 400 | `INVALID_REQUEST` |
| 401 | missing or invalid API key |
| 409 | `REQUEST_IN_PROGRESS` |
| 415 | `UNSUPPORTED_MEDIA` |
| 422 | `VIDEO_URL_EXPIRED`, `VIDEO_DOWNLOAD_FAILED`, `NO_SPEECH_DETECTED` |
| 429 | `RATE_LIMITED` (with `Retry-After`) |
| 500 | `PROCESSING_FAILED`, `UPLOAD_FAILED`, `INTERNAL_ERROR` |
| 503 | `SERVICE_UNAVAILABLE` (with `Retry-After`) |
| 504 | `TIMEOUT` |

The interface between the server and the two lanes is internal and documented in [`contracts/worker-api.md`](contracts/worker-api.md).

### Rules for changing a contract

1. Update the matching file in `contracts/` first.
2. Bump the version (`info.version` in the OpenAPI file, or the version noted in an internal contract).
3. Update the models in every project that sends or receives that shape.

## Repository structure

```
readright-ai/
├── server/          # HTTP entry: download, preprocess, fan-out, mini audio upload, merge
├── audio/           # audio lane (CrisperWhisper)
├── video/           # video lane (models to be chosen)
├── contracts/       # be-mai-openapi.yaml, and internal lane contracts
├── docs/            # deployment-architecture.md
└── README.md
```

Each project keeps its own `.env.example` (see `server/.env.example`). `compose.yaml` and a `Caddyfile` will be added at the root when the services are packaged.

Each of `server/`, `audio/` and `video/` is an independent Python project with its own `pyproject.toml`, `Dockerfile` and `tests/`. They share no code. Where they need to agree on a data shape, the agreement is documented in `contracts/` and each project keeps its own models.

## Deployment

MAI runs on one GPU instance (EC2 g5.2xlarge, ap-southeast-1) as Docker Compose containers: `caddy` (TLS), `api` (the server), `audio-worker`, `video-worker` and `redis` (idempotency state). The instance is started for sessions and stopped in between. See [`docs/deployment-architecture.md`](docs/deployment-architecture.md) for the full design, time budget and cost estimate.

## Getting started

### Current state

| Project | Status |
|---|---|
| `server/` | Runs locally with Redis. `POST /v1/analyses` runs end to end against the lanes (Phase 5 of 7, see [server/ROADMAP.md](server/ROADMAP.md)): `request_id` idempotency, a 115 s deadline, video download, ffmpeg preprocessing, parallel audio/video lane calls, result merging, and cutting and uploading the mini audio files to S3. Fake workers stand in for the lanes. |
| `audio/`, `video/` | Not started. |
| Docker Compose | Not yet (Phase 7). |

### Run the server locally

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip), a local Redis (`docker run -d -p 6379:6379 redis:7`; on Windows, Docker Desktop or Memurai) and `ffmpeg`. Requests with miscues also need an S3 bucket and AWS credentials with **write** access to it (cutting and uploading the mini audio files is done, Phase 5). Downloading the recording needs no credentials, because the BE sends a presigned URL. Only the server needs AWS settings; the audio and video lanes take no cloud configuration.

```bash
cd server
uv sync --extra dev
cp .env.example .env          # then set MAI_API_KEY
uv run uvicorn app.main:create_app --factory --reload --port 8000
```

Open http://localhost:8000/docs to try the API. Full setup, configuration, test and troubleshooting steps are in [`server/README.md`](server/README.md).

### What later phases will add

- `docker compose up --build` to run everything together (Phase 7).

Each project has its own `pyproject.toml` and tests. Because the lanes don't depend on S3, they can be developed and tested with local files in their `tests/fixtures/` folders. The server's tests use fake lanes and a fake presigned URL.

## Known limitations

- **Mispronunciations can be hidden.** The speech model may write a mispronounced word as the correct word, so the audio lane cannot flag it.
- **Teacher prompts are transcribed as reading.** If a teacher says a word aloud for the student, it appears in the transcript as if the student read it.
- **Moved phrases must match exactly.** A phrase read elsewhere with a changed word is reported as separate omissions and insertions.

## Model licensing

The CrisperWhisper model weights are released under a non-commercial research license. The inference code is MIT-licensed, but the weights are not. The license of the 2.0 weights is not yet confirmed. Check the license before any commercial use of this project.
