# Worker API (server to lanes)

Version: 1.0.0

Internal HTTP interface between the MAI server and each lane (audio, video) on the Docker network. Lanes never touch S3, never call each other and never talk to the BE. Files on the shared scratch volume are read-only for lanes; the server owns and deletes them.

The server is the only client. Unknown extra fields in a lane's result are ignored by the server.

Every 200 result MUST echo the request's `job_id`. The server rejects a result whose `job_id` differs (case-insensitive) with 500 `PROCESSING_FAILED`, so a mixed-up response never reaches another request. Numbers in results must be finite (no NaN or Infinity), `wpm` must be >= 0, and `reading_score` must be between 0 and 100 inclusive.

## POST /v1/jobs

### Audio request

```json
{
  "job_id": "<uuid, the request_id>",
  "deadline": 1760000000000,
  "wav_path": "/scratch/<request_id>/audio.wav",
  "passage_title": "...",
  "passage_content": "..."
}
```

`deadline` is an absolute UTC epoch time in integer milliseconds. `wav_path` is an absolute path on the shared scratch volume.

### Audio response (200)

```json
{
  "job_id": "<uuid>",
  "reading_score": 91.5,
  "wpm": 72.0,
  "is_word_by_word": false,
  "is_disregard_punctuation": true,
  "is_monotonous": false,
  "is_hardly_audible": false,
  "miscues": [
    {
      "actual_phrase": "cat",
      "spoken_phrase": "cap",
      "actual_phoneme": ["k", "ae", "t"],
      "spoken_phoneme": ["k", "ae", "p"],
      "start_ms": 1000,
      "end_ms": 1400,
      "miscue_type": "substitution"
    }
  ]
}
```

`start_ms` is an integer >= 0 and `end_ms` an integer >= `start_ms`. `miscue_type` uses the same values as the BE contract (`mispronunciation`, `omission`, `substitution`, `insertion`, `repetition`, `transposition`, `reversal`, `self-correction`).

The audio lane owns `reading_score`, `wpm`, `is_word_by_word`, `is_disregard_punctuation`, `is_monotonous`, `is_hardly_audible` and the miscues.

### Video request

```json
{ "job_id": "<uuid>", "deadline": 1760000000000, "webm_path": "/scratch/<request_id>/input.webm" }
```

### Video response (200)

```json
{ "job_id": "<uuid>", "is_finger_pointing": false, "is_lack_expression": false }
```

The video lane owns exactly `is_finger_pointing` and `is_lack_expression`. More checks may be added later.

### Errors

Any non-2xx response has this body:

```json
{ "error": { "code": "<CODE>", "message": "..." } }
```

| Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_JOB` | The job body is invalid. |
| 422 | `MEDIA_UNREADABLE` | The file is missing or cannot be decoded. |
| 422 | `NO_SPEECH` | No speech in the recording (audio lane only). |
| 504 | `DEADLINE_EXCEEDED` | The job's `deadline` has passed. |
| 500 | `INTERNAL` | Anything else. |

A lane drops (does not process) a job whose `deadline` has passed and responds 504 `DEADLINE_EXCEEDED`.

### How the server maps lane errors

| Lane response | Server error |
|---|---|
| Audio `NO_SPEECH` | 422 `NO_SPEECH_DETECTED` |
| `DEADLINE_EXCEEDED` | 504 `TIMEOUT` |
| Any other non-2xx, a non-JSON or invalid body, or a connection error | 500 `PROCESSING_FAILED` |
| Connection error after the request deadline has passed | 504 `TIMEOUT` |

Worker error messages are logged by the server, not returned to the BE.

### Cancellation

The server cancels a job by closing the HTTP connection. A lane should stop work for a job whose connection is closed where it can.

## GET /health/ready

200 `{"status": "ready"}` when the models are loaded, else 503.

## GET /stats

200 `{"queue_length": 0, "avg_processing_seconds": 1.2}`. `avg_processing_seconds` is a recent average, or `null` before the first job.

## Future (not v1)

`DELETE /v1/jobs/{job_id}`: explicit cancel endpoint.
