"""Request, response and error models of the worker API (contracts/worker-api.md 1.0.0)."""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt, StrictStr


class ErrorCode(StrEnum):
    INVALID_JOB = "INVALID_JOB"
    MEDIA_UNREADABLE = "MEDIA_UNREADABLE"
    NO_SPEECH = "NO_SPEECH"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INTERNAL = "INTERNAL"


class AudioJob(BaseModel):
    job_id: UUID
    deadline: StrictInt
    wav_path: StrictStr
    passage_title: StrictStr
    passage_content: StrictStr


class WorkerMiscue(BaseModel):
    actual_phrase: str
    spoken_phrase: str
    actual_phoneme: list[str]
    spoken_phoneme: list[str]
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(ge=0)]
    miscue_type: str


class AudioWorkerResult(BaseModel):
    job_id: UUID
    reading_score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    wpm: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    is_word_by_word: bool
    is_disregard_punctuation: bool
    is_monotonous: bool
    is_hardly_audible: bool
    miscues: list[WorkerMiscue]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_JOB: 400,
    ErrorCode.MEDIA_UNREADABLE: 422,
    ErrorCode.NO_SPEECH: 422,
    ErrorCode.DEADLINE_EXCEEDED: 504,
    ErrorCode.INTERNAL: 500,
}


class LaneError(Exception):
    """Raise this for any error the contract names; the status comes from STATUS_BY_CODE."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        return STATUS_BY_CODE[self.code]
