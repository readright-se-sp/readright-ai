from dataclasses import dataclass
from fnmatch import fnmatchcase

import numpy as np

from .config import EXPRESSION_GROUP_NAMES, Settings
from .landmarks import FrameFeatures

EXCLUDED_BLENDSHAPES: tuple[str, ...] = (
    "_neutral",
    "jaw*",
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "mouthLeft",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthLowerDown*",
    "mouthUpperUp*",
    "mouthStretch*",
    "cheekPuff",
    "eyeBlink*",
    "eyeLook*",
)

EXPRESSION_GROUPS: dict[str, tuple[str, ...]] = {
    "brow_raise": ("browInnerUp", "browOuterUpLeft", "browOuterUpRight"),
    "brow_down": ("browDownLeft", "browDownRight"),
    "smile": ("mouthSmileLeft", "mouthSmileRight", "mouthDimpleLeft", "mouthDimpleRight"),
    "frown": ("mouthFrownLeft", "mouthFrownRight", "mouthPressLeft", "mouthPressRight"),
    "squint": ("eyeSquintLeft", "eyeSquintRight", "cheekSquintLeft", "cheekSquintRight"),
    "eye_wide": ("eyeWideLeft", "eyeWideRight"),
    "sneer": ("noseSneerLeft", "noseSneerRight"),
}

# config.py cannot import this module (cycle through landmarks), so it keeps the names.
if set(EXPRESSION_GROUPS) != EXPRESSION_GROUP_NAMES:
    raise RuntimeError("EXPRESSION_GROUPS and config.EXPRESSION_GROUP_NAMES differ")


@dataclass(frozen=True)
class ExpressionResult:
    is_lack_expression: bool
    reason: str  # "flat" | "expressive" | "insufficient_face"
    coverage: float
    face_frames: int
    variability: dict[str, float]  # group -> p95 - p5; {} for insufficient_face


def _is_excluded(name: str) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in EXCLUDED_BLENDSHAPES)


def _group_series(blendshapes: list[dict[str, float]], members: tuple[str, ...]) -> list[float]:
    members = tuple(m for m in members if not _is_excluded(m))
    series = []
    for frame in blendshapes:
        # Non-finite scores (bad model output) are dropped like missing members.
        values = [frame[m] for m in members if m in frame and np.isfinite(frame[m])]
        if values:
            series.append(float(np.mean(values)))
    return series


def _smooth(series: list[float], window: int) -> np.ndarray:
    half = window // 2
    values = np.asarray(series, dtype=float)
    return np.array(
        [np.median(values[max(0, i - half) : i + half + 1]) for i in range(len(values))]
    )


def assess_expression(features: list[FrameFeatures], settings: Settings) -> ExpressionResult:
    faces = [f.face.blendshapes for f in features if f.face is not None]
    total = len(features)
    coverage = len(faces) / total if total else 0.0
    if (
        total == 0
        or len(faces) < settings.expression_min_face_frames
        or coverage < settings.expression_min_face_coverage
    ):
        return ExpressionResult(False, "insufficient_face", coverage, len(faces), {})

    variability: dict[str, float] = {}
    for group, members in EXPRESSION_GROUPS.items():
        series = _group_series(faces, members)
        if not series:
            variability[group] = 0.0
            continue
        smoothed = _smooth(series, settings.expression_smooth_frames)
        variability[group] = float(np.percentile(smoothed, 95) - np.percentile(smoothed, 5))

    lack = all(
        variability[g] < settings.expression_group_min_range.get(g, settings.expression_min_range)
        for g in EXPRESSION_GROUPS
    )
    return ExpressionResult(
        lack, "flat" if lack else "expressive", coverage, len(faces), variability
    )
