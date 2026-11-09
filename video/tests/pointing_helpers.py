"""Helpers for pointing tests: synthetic hands, poses and frame sequences.

Hand geometry (world landmarks, metres): wrist at the origin, palm along +y, fingers curl toward
the palm along -z. Each finger is a planar chain wrist -> MCP -> PIP -> DIP -> TIP with bones of
0.04 / 0.025 / 0.02 m (MCP sits 0.09 m from the wrist). A finger is described by three bend angles
in degrees: at MCP, at PIP and at DIP. The brief's `bend(PIP)` and `bend(DIP)` are the 2nd and 3rd.

Hand kinds:
- pointing: index straight; middle, ring and pinky curled (PIP bend 100, DIP bend 50).
- fist: every finger curled, including the index (PIP bend 100), so the index is not extended.
- open: every finger straight, so no finger is curled (fewer than 2 of middle/ring/pinky).
- tablet grip: every finger bent 90 degrees at the MCP toward the palm but only mildly curled at
  PIP/DIP (index 10/10, others 25/15). Not pointing because fewer than 2 of middle/ring/pinky are
  curled: their PIP bend (25) is below the curled threshold (60) and their tips stay farther from
  the wrist than their MCPs. (The index counts as extended here; the rule needs both.)

`image` coordinates are normalized; every point shares x = `tip_x - 0.05` except the index tip
(landmark 8), whose x is exactly `tip_x`, so cue B tests control it.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import replace

import numpy as np

from app.landmarks import FrameFeatures, HandSample, PoseSample

FRAME_STEP_MS = 200
DEFAULT_SCORE = 0.9

Bends = tuple[float, float, float]  # bend at MCP, PIP, DIP (degrees)

_FINGER_X = {"index": -0.03, "middle": -0.01, "ring": 0.01, "pinky": 0.03}
_FINGER_FIRST = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
_BONES = (0.04, 0.025, 0.02)
_MCP_DISTANCE = 0.09

STRAIGHT: Bends = (0, 0, 0)
CURLED: Bends = (70, 100, 50)
FIST_INDEX: Bends = (80, 100, 60)

HAND_KINDS = ("pointing", "fist", "open", "tablet")


def finger_bends(kind: str) -> dict[str, Bends]:
    if kind == "pointing":
        return {"index": STRAIGHT, "middle": CURLED, "ring": CURLED, "pinky": CURLED}
    if kind == "fist":
        return {"index": FIST_INDEX, "middle": CURLED, "ring": CURLED, "pinky": CURLED}
    if kind == "open":
        return {name: STRAIGHT for name in _FINGER_X}
    if kind == "tablet":
        grip: Bends = (90, 25, 15)
        return {"index": (90, 10, 10), "middle": grip, "ring": grip, "pinky": grip}
    raise ValueError(kind)


def world_from_bends(bends: dict[str, Bends]) -> np.ndarray:
    """21x3 world landmarks from per-finger (MCP, PIP, DIP) bend angles."""
    world = np.zeros((21, 3))
    # Thumb: a straight chain going up and out, 0.03 m per bone.
    thumb_dir = np.array([-1.0, 1.0, 0.0]) / math.sqrt(2)
    for k in range(1, 5):
        world[k] = thumb_dir * 0.03 * k
    for name, (mcp, pip, dip) in bends.items():
        first = _FINGER_FIRST[name]
        world[first] = (_FINGER_X[name], _MCP_DISTANCE, 0.0)
        angle = 0.0
        point = world[first].copy()
        for step, (bone, bend) in enumerate(zip(_BONES, (mcp, pip, dip), strict=True)):
            angle += math.radians(bend)
            point = point + bone * np.array([0.0, math.cos(angle), -math.sin(angle)])
            world[first + 1 + step] = point
    return world


def make_hand(kind: str, score: float = DEFAULT_SCORE, tip_x: float = 0.5) -> HandSample:
    world = world_from_bends(finger_bends(kind))
    image = np.zeros((21, 3))
    image[:, 0] = tip_x - 0.05
    image[:, 1] = 0.5
    image[8, 0] = tip_x
    return HandSample(world=world, image=image, handedness="Right", score=score)


def with_bends(hand_bends: dict[str, Bends], score: float = DEFAULT_SCORE) -> HandSample:
    hand = make_hand("open", score)
    return replace(hand, world=world_from_bends(hand_bends))


def rotation_matrix(axis: Sequence[float], degrees: float) -> np.ndarray:
    """Rodrigues rotation about `axis` (any length) by `degrees`."""
    k = np.asarray(axis, dtype=float)
    k = k / np.linalg.norm(k)
    theta = math.radians(degrees)
    cross = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(theta) * cross + (1 - math.cos(theta)) * (cross @ cross)


def transformed(hand: HandSample, rotation: np.ndarray, shift: Sequence[float]) -> HandSample:
    return replace(hand, world=hand.world @ rotation.T + np.asarray(shift, dtype=float))


ROTATIONS = [
    (rotation_matrix((1, 0, 0), 90), (0.0, 0.0, 0.0)),
    (rotation_matrix((0, 1, 0), 180), (0.1, -0.2, 0.3)),
    (rotation_matrix((1, 1, 1), 120), (0.0, 0.5, 0.0)),
    (rotation_matrix((2, -1, 0.5), 37), (-0.3, 0.2, 0.1)),
    (rotation_matrix((0, 0.3, 1), 250), (1.0, 1.0, 1.0)),
]


def make_pose(
    *,
    left: tuple[float, float, float, float] | None = None,
    right: tuple[float, float, float, float] | None = None,
    shoulders_x: tuple[float, float] = (0.35, 0.65),
) -> PoseSample:
    """33x4 pose. `left` / `right` = (wrist_x, wrist_y, wrist_visibility, elbow_y).

    Omitted sides rest with the wrist at mid-height (y 0.5), so they never satisfy the bottom band.
    """
    marks = np.zeros((33, 4))
    marks[:, 3] = 1.0
    marks[:, 1] = 0.5
    marks[11] = (shoulders_x[0], 0.3, 0.0, 1.0)
    marks[12] = (shoulders_x[1], 0.3, 0.0, 1.0)
    marks[13] = (shoulders_x[0], 0.4, 0.0, 1.0)
    marks[14] = (shoulders_x[1], 0.4, 0.0, 1.0)
    marks[15] = (shoulders_x[0], 0.5, 0.0, 1.0)
    marks[16] = (shoulders_x[1], 0.5, 0.0, 1.0)
    for wrist_idx, elbow_idx, side in ((15, 13, left), (16, 14, right)):
        if side is None:
            continue
        wx, wy, vis, ey = side
        marks[wrist_idx] = (wx, wy, 0.0, vis)
        marks[elbow_idx] = (wx, ey, 0.0, 1.0)
    return PoseSample(marks)


def arm_down_pose() -> PoseSample:
    """Left wrist low and centred, elbow lower still: the arm-toward-screen pose."""
    return make_pose(left=(0.45, 0.85, 1.0, 0.95))


def resting_pose() -> PoseSample:
    """Both wrists at mid-height: never counts as arm toward the screen."""
    return make_pose()


HandsAt = Callable[[int], list[HandSample]]
PoseAt = Callable[[int], PoseSample | None]


def make_features(
    count: int, hands_at: HandsAt | None = None, pose_at: PoseAt | None = None
) -> list[FrameFeatures]:
    """`count` frames at 5 fps (timestamps i * 200), no face."""
    return [
        FrameFeatures(
            i * FRAME_STEP_MS,
            None,
            hands_at(i) if hands_at else [],
            pose_at(i) if pose_at else None,
        )
        for i in range(count)
    ]


def hands_in_range(start: int, stop: int, kind: str = "pointing") -> HandsAt:
    """One `kind` hand on frames start <= i < stop, none elsewhere."""
    hand = make_hand(kind)
    return lambda i: [hand] if start <= i < stop else []


def sawtooth_xs(
    periods: int,
    *,
    mirrored: bool = False,
    steps: int = 10,
    step: float = 0.04,
    trailing: int = 3,
) -> list[float]:
    """Slow ramp over `steps` intervals (2 s at 5 fps), one-frame return, repeated `periods` times.

    A few `trailing` frames at the start value follow, so the last sweep has its return in view.
    """
    xs: list[float] = []
    for _ in range(periods):
        xs.extend(0.2 + step * k for k in range(steps + 1))
    xs.extend([0.2] * trailing)
    return [0.8 - x for x in xs] if mirrored else xs


def timestamps(count: int) -> list[int]:
    return [i * FRAME_STEP_MS for i in range(count)]
