# readright-ai

The MAI (Microservices AI) is the AI layer of ReadRight, an oral reading assessment platform. The BE (AWS Lambda) calls `POST /v1/analyses` with a presigned recording URL and a passage; the MAI returns the analysis.

- [README.md](README.md): how the system works, API summary, current state.
- [docs/deployment-architecture.md](docs/deployment-architecture.md): deployment, time budget, cost.
- [contracts/be-mai-openapi.yaml](contracts/be-mai-openapi.yaml): BE-MAI API contract (source of truth).
- [contracts/worker-api.md](contracts/worker-api.md): server-lane (audio/video worker) HTTP contract.

## Layout

- `server/`: FastAPI entry point. See [server/CLAUDE.md](server/CLAUDE.md).
- `audio/`, `video/`: analysis lanes. Not started yet.
- `contracts/`: shared shapes live only here.
- Each of `server/`, `audio/`, `video/` is an independent Python project. They share no code.

## Contract rules

- `contracts/be-mai-openapi.yaml` is the source of truth for the BE-MAI API.
- To change a shape: update the contract first, bump `info.version`, then update the models in every project that uses it.
- `__version__` in `server/app/__init__.py` must equal the contract version. `server/tests/test_contract.py` enforces this.
- Never invent error codes or fields that are not in the contract. Flag gaps to the user instead.
- Known gap: 401 uses code `INVALID_REQUEST` because the contract has no auth error code.

## Agreed design decisions

Do not change these without the user's approval.

- The server downloads the presigned `video_url` and converts it to one WAV (16 kHz, 16-bit PCM, mono). WAV + passage go to the audio lane; the webm goes to the video lane. The lanes run in parallel.
- The audio lane returns miscues with start/end times. The server cuts the mini audio files from the WAV and uploads them to S3. Lanes never touch S3, never call each other, never talk to the BE.
- Mini audio files are WAV (same format). Keys are `<prefix>miscue-0001.wav`, ... The default prefix is `analyses/<request_id>/miscues/`.
- A video lane failure fails the whole request. `MAI_ALLOW_AUDIO_ONLY_ON_VIDEO_FAILURE` (default false) allows an audio-only result instead.
- The API is synchronous with a 120 s SLA. The internal deadline is 115 s and returns `504 TIMEOUT`.

## Workflow

Each project is built in phases described in its own roadmap. Before planning or implementing work in a project, read its roadmap and follow its "How to work a phase" steps.

| Project | Roadmap |
|---|---|
| `server/` | [server/ROADMAP.md](server/ROADMAP.md) |
| `audio/` | Not created yet |
| `video/` | Not created yet |

When code or decisions change, keep [README.md](README.md), the project's README and [docs/deployment-architecture.md](docs/deployment-architecture.md) consistent with them.

## Safety

- Never read, print or modify `.env` files or API key values. `.env.example` is fine.
- Never log key values or full presigned URLs. Log the host only.
- Never commit unless asked.
