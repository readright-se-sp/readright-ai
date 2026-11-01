from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, HttpUrl, field_validator


class AnalysisRequest(BaseModel):
    request_id: UUID
    video_url: HttpUrl
    passage_title: str
    passage_content: str
    output_key_prefix: str | None = None

    @field_validator("passage_title", "passage_content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty or whitespace-only")
        return value


class MiscueType(StrEnum):
    MISPRONUNCIATION = "mispronunciation"
    OMISSION = "omission"
    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    REPETITION = "repetition"
    TRANSPOSITION = "transposition"
    REVERSAL = "reversal"
    SELF_CORRECTION = "self-correction"


class Miscue(BaseModel):
    actual_phrase: str
    spoken_phrase: str
    actual_phoneme: list[str]
    spoken_phoneme: list[str]
    read_start: int
    read_end: int
    audio_object_key: str
    miscue_type: MiscueType


class AnalysisResponse(BaseModel):
    request_id: UUID
    reading_score: float
    wpm: float
    is_word_by_word: bool
    is_disregard_punctuation: bool
    is_monotonous: bool
    is_lack_expression: bool
    is_hardly_audible: bool
    is_finger_pointing: bool
    miscues: list[Miscue]


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    VIDEO_DOWNLOAD_FAILED = "VIDEO_DOWNLOAD_FAILED"
    VIDEO_URL_EXPIRED = "VIDEO_URL_EXPIRED"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    NO_SPEECH_DETECTED = "NO_SPEECH_DETECTED"
    REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
