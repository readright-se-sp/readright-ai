# readright-api

The AI layer of **ReadRight**, an oral reading assessment platform based on the Phil-IRI. It takes a recording of a student reading a passage aloud and returns an analysis of the reading: what was said, how it lines up with the passage, and what the video shows.

This layer only processes media. It knows nothing about students, classes or assessments; those belong to the backend-for-frontend (BFF).

```
Frontend  ⇄  BFF  ⇄  readright-api (this repo)
```

## How it works

```
BFF ──(S3 file id, passage text)──► server
                                      ├─ fetches the recording from S3
                                      ├─ preprocesses audio, video and passage
                                      ├──► audio service ──┐
                                      └──► video service ──┤   (run in parallel)
                                      ◄── merges results ◄─┘
BFF ◄── analysis response
```

| Project | Role |
|---|---|
| `server/` | The only HTTP entry point. Fetches media from S3, preprocesses it, sends it to the analysis services and merges their results. |
| `audio/` | Transcribes the reading verbatim and aligns it with the passage to tag miscues. |
| `video/` | Analyzes the reader on video. |

The audio and video services never fetch from S3, never call each other and never talk to the BFF. They receive ready-to-use data from the server and return results.

## Repository structure

```
readright-api/
├── server/          # HTTP entry: S3 fetch, preprocessing, fan-out, merge
├── audio/           # audio analysis microservice
├── video/           # video analysis microservice
├── contracts/       # request/response samples and tag definitions
├── scripts/         # benchmarks and local dev helpers
├── compose.yaml     # runs all services together with a shared volume
├── .env.example
└── README.md
```

Each of `server/`, `audio/` and `video/` is an independent Python project with its own `pyproject.toml`, `Dockerfile` and `tests/`. They share no code. Where they need to agree on a data shape, the agreement is documented in `contracts/` and each project keeps its own models.

## Getting started

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for running a service outside Docker)
- `ffmpeg` (for preprocessing in the server)
- AWS credentials with read access to the recordings bucket

### Configuration

```bash
cp .env.example .env
# fill in the S3 bucket, region and credentials
```

Only the server needs S3 settings. The audio and video services take no cloud configuration.

### Run everything

```bash
docker compose up --build
```

### Run one service on its own

```bash
cd audio            # or server/, video/
pip install -e ".[dev]"
pytest
```

Because the audio and video services don't depend on S3, you can develop and test them using local files in their `tests/fixtures/` folders.

## The API contract

The BFF sends the server two things:

- `s3_file_id`: the recording to analyze
- `passage`: the text the student was asked to read

The server responds with the audio and video results combined. Full request and response samples are in `contracts/server-api.md`.

### Audio tags

The audio service returns one row per aligned word:

```
(passage_word, spoken_word, start, end, tag, move_id)
```

`passage_word` is empty for words that were only spoken, and `spoken_word`, `start` and `end` are empty for words that were skipped. Times are in seconds from the start of the recording.

| Tag | Meaning |
|---|---|
| `match` | Read correctly |
| `substitution` | A different word was read |
| `omission` | A passage word was skipped |
| `insertion` | An extra word was read |
| `swap` | Two adjacent words were read in reverse order |
| `repetition` | A word or phrase was repeated |
| `moved` | A phrase was read in a different place; both halves share a `move_id` |

Tags derived after alignment (such as self-correction, reversal and mispronunciation) are documented in `contracts/audio-service.md`.

### Rules for changing a contract

1. Update the matching file in `contracts/` first.
2. Bump the `version` field in the affected response.
3. Update the models in every project that sends or receives that shape.

## Known limitations

- **Mispronunciations can be hidden.** The speech model may write a mispronounced word as the correct word, so the audio service cannot flag it.
- **Teacher prompts are transcribed as reading.** If a teacher says a word aloud for the student, it appears in the transcript as if the student read it.
- **Moved phrases must match exactly.** A phrase read elsewhere with a changed word is reported as separate omissions and insertions.

## Model licensing

The CrisperWhisper model weights are released under a non-commercial research license. The inference code is MIT-licensed, but the weights are not. Check the license before any commercial use of this project.