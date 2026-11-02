import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .config import Settings
from .landmarks import FrameFeatures, HandSample, PoseSample

if TYPE_CHECKING:
    from .frames import SamplePlan

# name -> (MCP, PIP, DIP, TIP) landmark indices
FINGERS: dict[str, tuple[int, int, int, int]] = {
    "thumb": (1, 2, 3, 4),
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}

_POSE_SIDES = ((11, 13, 15), (12, 14, 16))  # shoulder, elbow, wrist
_POSE_X_MARGIN = 0.1
_SWEEP_RETREAT = 0.01  # a sweep run survives a retreat up to this much (normalized x)
_SWEEP_SMOOTH_FRAMES = 3


@dataclass(frozen=True)
class PointingResult:
    is_finger_pointing: bool
    reason: str  # "shape" | "sweeps" | "both" | "none" | "no_hands"
    stats: dict[str, float | int | str]


def _bend(prev: np.ndarray, joint: np.ndarray, nxt: np.ndarray) -> float:
    """Angle in degrees between prev->joint and joint->next; nan for a zero-length bone."""
    a = joint - prev
    b = nxt - joint
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if norm == 0 or not math.isfinite(norm):
        return math.nan
    cos = float(np.dot(a, b)) / norm
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def bend_angles(world: np.ndarray) -> dict[str, tuple[float, float]]:
    """Per finger: (bend at PIP, bend at DIP) in degrees; 0 is straight."""
    angles: dict[str, tuple[float, float]] = {}
    for name, (mcp, pip, dip, tip) in FINGERS.items():
        angles[name] = (
            _bend(world[mcp], world[pip], world[dip]),
            _bend(world[pip], world[dip], world[tip]),
        )
    return angles


def _dist(world: np.ndarray, i: int) -> float:
    return float(np.linalg.norm(world[i] - world[0]))


def _extended(world: np.ndarray, name: str, bends: tuple[float, float], limit: float) -> bool:
    _, pip, _, tip = FINGERS[name]
    return bool(bends[0] < limit and bends[1] < limit and _dist(world, tip) > _dist(world, pip))


def _curled(world: np.ndarray, name: str, bends: tuple[float, float], limit: float) -> bool:
    mcp, _, _, tip = FINGERS[name]
    return bool(bends[0] >= limit or _dist(world, tip) < _dist(world, mcp))


def is_pointing_hand(hand: HandSample, settings: Settings) -> bool:
    if hand.score < settings.pointing_min_hand_score:
        return False
    world = hand.world
    bends = bend_angles(world)
    if not _extended(world, "index", bends["index"], settings.pointing_extended_max_bend_deg):
        return False
    curled = sum(
        _curled(world, name, bends[name], settings.pointing_curled_min_bend_deg)
        for name in ("middle", "ring", "pinky")
    )
    return curled >= 2


def arm_toward_screen(pose: PoseSample, settings: Settings) -> bool:
    marks = pose.landmarks
    shoulder_xs = (marks[11, 0], marks[12, 0])
    low, high = min(shoulder_xs) - _POSE_X_MARGIN, max(shoulder_xs) + _POSE_X_MARGIN
    for _, elbow, wrist in _POSE_SIDES:
        x, y, _, visibility = marks[wrist]
        if (
            visibility >= settings.pointing_pose_min_visibility
            and y >= 1 - settings.pointing_pose_bottom_band
            and low <= x <= high
            and marks[elbow, 1] > y
        ):
            return True
    return False


def episodes(
    evidence: list[bool], timestamps_ms: list[int], interval_ms: float, settings: Settings
) -> list[tuple[int, int]]:
    """Kept evidence episodes as inclusive (first_index, last_index) pairs, in order."""
    runs: list[list[int]] = []
    for i, flag in enumerate(evidence):
        if not flag:
            continue
        if runs and runs[-1][1] == i - 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])

    merged: list[list[int]] = []
    for first, last in runs:
        if merged:
            gap = timestamps_ms[first] - timestamps_ms[merged[-1][1]] - interval_ms
            if gap <= settings.pointing_merge_gap_ms:
                merged[-1][1] = last
                continue
        merged.append([first, last])

    return [
        (first, last)
        for first, last in merged
        if timestamps_ms[last] - timestamps_ms[first] + interval_ms
        >= settings.pointing_min_episode_ms
    ]


def _median_smooth(values: list[float]) -> list[float]:
    half = _SWEEP_SMOOTH_FRAMES // 2
    return [float(np.median(values[max(0, i - half) : i + half + 1])) for i in range(len(values))]


def _segment_sweeps(xs: list[float], ts: list[int], settings: Settings) -> int:
    count = 0
    i = 0
    n = len(xs)
    while i < n - 1:
        direction = 0
        extreme = i
        for j in range(i + 1, n):
            delta = xs[j] - xs[extreme]
            if direction == 0:
                if delta != 0:
                    direction = 1 if delta > 0 else -1
                    extreme = j
            elif direction * delta > 0:
                extreme = j
            elif direction * delta < -_SWEEP_RETREAT:
                break
        if extreme == i:
            break
        dx = xs[extreme] - xs[i]
        if (
            ts[extreme] - ts[i] >= settings.sweep_min_ms
            and abs(dx) >= settings.sweep_min_dx
            and _returns(xs, ts, extreme, direction, abs(dx), settings)
        ):
            count += 1
        i = extreme
    return count


def _returns(
    xs: list[float], ts: list[int], extreme: int, direction: int, size: float, settings: Settings
) -> bool:
    best = 0.0
    for k in range(extreme + 1, len(xs)):
        if ts[k] - ts[extreme] > settings.sweep_return_max_ms:
            break
        best = max(best, direction * (xs[extreme] - xs[k]))
    return best >= size / 2


def count_sweeps(xs: list[float | None], timestamps_ms: list[int], settings: Settings) -> int:
    """Left-right fingertip sweeps that return, as when tracking lines of text."""
    # Non-finite x (bad model output) is a gap, like a missing hand.
    xs = [x if x is not None and math.isfinite(x) else None for x in xs]
    total = 0
    start = 0
    for end in range(len(xs) + 1):
        if end < len(xs) and xs[end] is not None:
            continue
        if end > start:
            segment = [float(x) for x in xs[start:end] if x is not None]
            total += _segment_sweeps(_median_smooth(segment), timestamps_ms[start:end], settings)
        start = end + 1
    return total


def assess_pointing(
    features: list[FrameFeatures], plan: "SamplePlan", settings: Settings
) -> PointingResult:
    interval_ms = 1000 / plan.fps
    recording_ms = len(features) * interval_ms
    timestamps = [f.timestamp_ms for f in features]

    shape = [any(is_pointing_hand(h, settings) for h in f.hands) for f in features]
    pose = [
        settings.use_pose
        and not f.hands
        and f.pose is not None
        and arm_toward_screen(f.pose, settings)
        for f in features
    ]
    evidence = [a or c for a, c in zip(shape, pose, strict=True)]

    kept = episodes(evidence, timestamps, interval_ms, settings)
    total_ms = float(
        sum(timestamps[last] - timestamps[first] + interval_ms for first, last in kept)
    )
    shape_decision = total_ms >= max(
        settings.pointing_min_total_ms, settings.pointing_min_total_fraction * recording_ms
    )

    sweeps = 0
    if settings.pointing_use_sweeps:
        xs: list[float | None] = [
            float(f.hands[0].image[8, 0]) if f.hands else None for f in features
        ]
        sweeps = count_sweeps(xs, timestamps, settings)
    sweep_decision = sweeps >= settings.sweep_min_count

    pose_frames = sum(pose)
    if not any(f.hands for f in features) and pose_frames == 0:
        reason = "no_hands"
    elif shape_decision and sweep_decision:
        reason = "both"
    elif shape_decision:
        reason = "shape"
    elif sweep_decision:
        reason = "sweeps"
    else:
        reason = "none"

    return PointingResult(
        is_finger_pointing=shape_decision or sweep_decision,
        reason=reason,
        stats={
            "pointing_frames": sum(evidence),
            "pointing_episodes": len(kept),
            "pointing_total_ms": total_ms,
            "sweeps": sweeps,
            "pose_frames": pose_frames,
            "pointing_reason": reason,
        },
    )
