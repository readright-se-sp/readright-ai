"""Helpers for landmark tests: model paths, skips, fake MediaPipe results and fake sessions."""

import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.frames import Frame
from app.landmarks import FrameFeatures

REAL_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
FACE = "face_landmarker.task"
HAND = "hand_landmarker.task"
POSE = "pose_landmarker_lite.task"


def require_models(use_pose: bool = False) -> Path:
    needed = [FACE, HAND] + ([POSE] if use_pose else [])
    missing = [name for name in needed if not (REAL_MODEL_DIR / name).is_file()]
    if missing:
        pytest.skip(f"model files missing in {REAL_MODEL_DIR}: {', '.join(missing)}")
    return REAL_MODEL_DIR


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_lock(path: Path, payloads: dict[str, bytes]) -> Path:
    lines = ["# test lock", ""]
    for name, data in payloads.items():
        lines.append(f"{name} https://example.invalid/{name} {sha(data)}")
    path.write_text("\n".join(lines) + "\n")
    return path


def point(x=0.0, y=0.0, z=0.0, visibility=None):
    values = {"x": x, "y": y, "z": z}
    if visibility is not None:
        values["visibility"] = visibility
    return SimpleNamespace(**values)


def category(name: str, score: float):
    return SimpleNamespace(category_name=name, score=score)


class FakeSession:
    """Session double: records processed frames and whether it was closed."""

    def __init__(self, features=None, error: Exception | None = None, error_at: int | None = None):
        self.features = features
        self.error = error
        self.error_at = error_at
        self.frames: list[Frame] = []
        self.closed = False
        self.entered = False

    def process(self, frame: Frame) -> FrameFeatures:
        self.frames.append(frame)
        if self.error is not None and (self.error_at is None or len(self.frames) == self.error_at):
            raise self.error
        if self.features is not None:
            return self.features(frame)
        return FrameFeatures(timestamp_ms=frame.timestamp_ms, face=None, hands=[], pose=None)

    def factory(self):
        @contextmanager
        def make():
            self.entered = True
            try:
                yield self
            finally:
                self.closed = True

        return make
