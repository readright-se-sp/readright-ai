import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from .config import Settings

if TYPE_CHECKING:
    from .frames import Frame

MODELS_LOCK = Path(__file__).resolve().parent.parent / "models.lock"

FACE_MODEL = "face_landmarker.task"
HAND_MODEL = "hand_landmarker.task"
POSE_MODEL = "pose_landmarker_lite.task"

_CHUNK = 1 << 20


@dataclass(frozen=True)
class LockEntry:
    name: str
    url: str
    sha256: str


def read_lock(path: Path = MODELS_LOCK) -> list[LockEntry]:
    entries: list[LockEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, url, digest = line.split()
        entries.append(LockEntry(name, url, digest.lower()))
    return entries


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FaceSample:
    blendshapes: dict[str, float]  # category_name -> score


@dataclass(frozen=True)
class HandSample:
    world: np.ndarray  # (21, 3) metres
    image: np.ndarray  # (21, 3) normalized x, y, z
    handedness: str  # MediaPipe label as given
    score: float  # handedness score


@dataclass(frozen=True)
class PoseSample:
    landmarks: np.ndarray  # (33, 4): x, y, z, visibility


@dataclass(frozen=True)
class FrameFeatures:
    timestamp_ms: int
    face: FaceSample | None
    hands: list[HandSample]
    pose: PoseSample | None


class ModelError(Exception):
    """A model file is missing or does not match models.lock. Message names the file."""


def face_from_result(result: Any) -> FaceSample | None:
    faces = result.face_blendshapes
    if not faces:
        return None
    return FaceSample({c.category_name: float(c.score) for c in faces[0]})


def hands_from_result(result: Any) -> list[HandSample]:
    hands = []
    for image, world, handed in zip(
        result.hand_landmarks, result.hand_world_landmarks, result.handedness, strict=True
    ):
        best = handed[0]
        hands.append(
            HandSample(
                world=np.array([[p.x, p.y, p.z] for p in world], dtype=float),
                image=np.array([[p.x, p.y, p.z] for p in image], dtype=float),
                handedness=best.category_name,
                score=float(best.score),
            )
        )
    hands.sort(key=lambda h: h.score, reverse=True)
    return hands


def pose_from_result(result: Any) -> PoseSample | None:
    poses = result.pose_landmarks
    if not poses:
        return None
    return PoseSample(np.array([[p.x, p.y, p.z, p.visibility] for p in poses[0]], dtype=float))


class ModelBundle:
    def __init__(self, settings: Settings, lock_path: Path = MODELS_LOCK) -> None:
        lock = {e.name: e for e in read_lock(lock_path)}
        self.face_path = self._check(settings.model_dir, FACE_MODEL, lock)
        self.hand_path = self._check(settings.model_dir, HAND_MODEL, lock)
        self.pose_path: Path | None = (
            self._check(settings.model_dir, POSE_MODEL, lock) if settings.use_pose else None
        )

    @staticmethod
    def _check(model_dir: Path, name: str, lock: dict[str, LockEntry]) -> Path:
        path = model_dir / name
        if name not in lock:
            raise ModelError(f"{name} is not listed in models.lock")
        if not path.is_file():
            raise ModelError(f"{name} is missing")
        if sha256_file(path) != lock[name].sha256:
            raise ModelError(f"{name} does not match models.lock")
        return path

    def validate(self) -> None:
        """Create and close one landmarker of each used kind."""
        with LandmarkerSession(self, _Options(use_pose=self.pose_path is not None)):
            pass


@dataclass(frozen=True)
class _Options:
    """The slice of Settings that LandmarkerSession reads."""

    use_pose: bool
    num_hands: int = 1


class LandmarkerSession:
    def __init__(self, bundle: ModelBundle, settings: Settings | _Options) -> None:
        self._bundle = bundle
        self._settings = settings
        self._face: Any = None
        self._hands: Any = None
        self._pose: Any = None
        self._open = False

    def __enter__(self) -> "LandmarkerSession":
        mode = vision.RunningMode.VIDEO
        try:
            self._face = vision.FaceLandmarker.create_from_options(
                vision.FaceLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(self._bundle.face_path)),
                    running_mode=mode,
                    num_faces=1,
                    output_face_blendshapes=True,
                )
            )
            self._hands = vision.HandLandmarker.create_from_options(
                vision.HandLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(self._bundle.hand_path)),
                    running_mode=mode,
                    num_hands=self._settings.num_hands,
                )
            )
            if self._settings.use_pose and self._bundle.pose_path is not None:
                self._pose = vision.PoseLandmarker.create_from_options(
                    vision.PoseLandmarkerOptions(
                        base_options=BaseOptions(model_asset_path=str(self._bundle.pose_path)),
                        running_mode=mode,
                        num_poses=1,
                    )
                )
        except BaseException:
            self.__exit__(None, None, None)
            raise
        self._open = True
        return self

    def __exit__(self, *exc: object) -> None:
        self._open = False
        first: Exception | None = None
        for name in ("_face", "_hands", "_pose"):
            landmarker = getattr(self, name)
            setattr(self, name, None)
            if landmarker is None:
                continue
            try:
                landmarker.close()
            except Exception as err:  # keep closing the others
                first = first or err
        if first is not None:
            raise first

    def process(self, frame: "Frame") -> FrameFeatures:
        if not self._open:
            raise RuntimeError("LandmarkerSession is not open")
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame.rgb))
        ts = frame.timestamp_ms
        face = face_from_result(self._face.detect_for_video(image, ts))
        hands = hands_from_result(self._hands.detect_for_video(image, ts))
        pose = pose_from_result(self._pose.detect_for_video(image, ts)) if self._pose else None
        return FrameFeatures(timestamp_ms=ts, face=face, hands=hands, pose=pose)
