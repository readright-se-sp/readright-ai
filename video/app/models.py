from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, StrictInt, StrictStr, StringConstraints


class VideoJob(BaseModel):
    job_id: Annotated[StrictStr, StringConstraints(min_length=1)]
    deadline: StrictInt
    webm_path: StrictStr


class VideoResult(BaseModel):
    job_id: str
    is_finger_pointing: bool
    is_lack_expression: bool


class ErrorCode(StrEnum):
    INVALID_JOB = "INVALID_JOB"
    MEDIA_UNREADABLE = "MEDIA_UNREADABLE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INTERNAL = "INTERNAL"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
