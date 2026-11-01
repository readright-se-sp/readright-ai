"""Builders for lane results and request settings shared by the lane and pipeline tests."""

from pathlib import Path
from typing import Any

from conftest import API_KEY

from app.config import Settings
from app.workers import AudioLaneResult, LaneMiscue, VideoLaneResult

REQUEST_ID = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"


def lane_miscue(**overrides: Any) -> dict[str, Any]:
    miscue: dict[str, Any] = {
        "actual_phrase": "cat",
        "spoken_phrase": "cap",
        "actual_phoneme": ["k", "ae", "t"],
        "spoken_phoneme": ["k", "ae", "p"],
        "start_ms": 1000,
        "end_ms": 1400,
        "miscue_type": "substitution",
    }
    miscue.update(overrides)
    return miscue


def audio_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": REQUEST_ID,
        "reading_score": 91.5,
        "wpm": 72.0,
        "is_word_by_word": False,
        "is_disregard_punctuation": True,
        "is_monotonous": False,
        "is_hardly_audible": False,
        "miscues": [lane_miscue()],
    }
    payload.update(overrides)
    return payload


def video_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": REQUEST_ID,
        "is_finger_pointing": True,
        "is_lack_expression": True,
    }
    payload.update(overrides)
    return payload


def audio_result(**overrides: Any) -> AudioLaneResult:
    return AudioLaneResult.model_validate(audio_payload(**overrides))


def video_result(**overrides: Any) -> VideoLaneResult:
    return VideoLaneResult.model_validate(video_payload(**overrides))


def make_lane_miscue(**overrides: Any) -> LaneMiscue:
    return LaneMiscue.model_validate(lane_miscue(**overrides))


def worker_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "scratch_dir": tmp_path / "scratch",
        "audio_worker_url": "http://audio.test:8001",
        "video_worker_url": "http://video.test:8002",
    }
    values.update(overrides)
    return Settings(_env_file=None, api_key=API_KEY, **values)  # type: ignore[call-arg]
