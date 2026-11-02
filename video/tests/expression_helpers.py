"""Helpers for expression tests: synthetic face frames built from blendshape dicts."""

from collections.abc import Callable

from app.expression import EXPRESSION_GROUPS
from app.landmarks import FaceSample, FrameFeatures

BASELINE = 0.2
FRAME_STEP_MS = 200

Overrides = Callable[[int], dict[str, float]]


def baseline_blendshapes() -> dict[str, float]:
    """Every grouped blendshape at a constant, in-range value."""
    return {name: BASELINE for members in EXPRESSION_GROUPS.values() for name in members}


def make_frames(count: int, overrides: Overrides | None = None) -> list[FrameFeatures]:
    """`count` frames with a face; `overrides(i)` adds or replaces blendshapes on frame i."""
    frames = []
    for i in range(count):
        shapes = baseline_blendshapes()
        if overrides is not None:
            shapes.update(overrides(i))
        frames.append(FrameFeatures(i * FRAME_STEP_MS, FaceSample(shapes), [], None))
    return frames


def block_step(group: str, step: float, count: int = 100) -> list[FrameFeatures]:
    """Raise every member of `group` by `step` over the middle 40% of the frames."""
    start, stop = int(count * 0.3), int(count * 0.7)

    def overrides(i: int) -> dict[str, float]:
        if start <= i < stop:
            return {name: BASELINE + step for name in EXPRESSION_GROUPS[group]}
        return {}

    return make_frames(count, overrides)


def no_face_frames(count: int) -> list[FrameFeatures]:
    return [FrameFeatures(i * FRAME_STEP_MS, None, [], None) for i in range(count)]
