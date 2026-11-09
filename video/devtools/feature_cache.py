"""Landmark-only feature cache (.npz) shared by the devtools. Stores no image data."""

from pathlib import Path

import numpy as np

from app.frames import SamplePlan
from app.landmarks import FaceSample, FrameFeatures, HandSample, PoseSample

CACHE_VERSION = 1


def save_cache(path: Path, features: list[FrameFeatures], plan: SamplePlan) -> None:
    n = len(features)
    names = sorted({name for f in features if f.face for name in f.face.blendshapes})
    index = {name: i for i, name in enumerate(names)}
    max_hands = max((len(f.hands) for f in features), default=0)

    face_present = np.zeros(n, dtype=bool)
    blendshapes = np.full((n, len(names)), np.nan)
    hand_count = np.zeros(n, dtype=int)
    hand_world = np.full((n, max_hands, 21, 3), np.nan)
    hand_image = np.full((n, max_hands, 21, 3), np.nan)
    hand_score = np.full((n, max_hands), np.nan)
    hand_handedness = np.full((n, max_hands), "", dtype="<U16")
    pose_present = np.zeros(n, dtype=bool)
    pose = np.full((n, 33, 4), np.nan)

    for i, f in enumerate(features):
        if f.face is not None:
            face_present[i] = True
            for name, score in f.face.blendshapes.items():
                blendshapes[i, index[name]] = score
        hand_count[i] = len(f.hands)
        for j, hand in enumerate(f.hands):
            hand_world[i, j] = hand.world
            hand_image[i, j] = hand.image
            hand_score[i, j] = hand.score
            hand_handedness[i, j] = hand.handedness
        if f.pose is not None:
            pose_present[i] = True
            pose[i] = f.pose.landmarks

    np.savez_compressed(
        path,
        version=CACHE_VERSION,
        fps=plan.fps,
        width=plan.width,
        height=plan.height,
        max_frames=plan.max_frames,
        timestamp_ms=np.array([f.timestamp_ms for f in features], dtype=np.int64),
        blendshape_names=np.array(names, dtype="<U64"),
        face_present=face_present,
        blendshapes=blendshapes,
        hand_count=hand_count,
        hand_world=hand_world,
        hand_image=hand_image,
        hand_score=hand_score,
        hand_handedness=hand_handedness,
        pose_present=pose_present,
        pose=pose,
    )


def load_cache(path: Path) -> tuple[list[FrameFeatures], SamplePlan]:
    with np.load(path) as data:
        if int(data["version"]) != CACHE_VERSION:
            raise ValueError(f"{path.name}: unsupported cache version {int(data['version'])}")
        plan = SamplePlan(
            fps=float(data["fps"]),
            width=int(data["width"]),
            height=int(data["height"]),
            max_frames=int(data["max_frames"]),
        )
        names = [str(n) for n in data["blendshape_names"]]
        features: list[FrameFeatures] = []
        for i, ts in enumerate(data["timestamp_ms"]):
            face = None
            if data["face_present"][i]:
                row = data["blendshapes"][i]
                face = FaceSample(
                    {n: float(v) for n, v in zip(names, row, strict=True) if not np.isnan(v)}
                )
            hands = [
                HandSample(
                    world=np.array(data["hand_world"][i, j]),
                    image=np.array(data["hand_image"][i, j]),
                    handedness=str(data["hand_handedness"][i, j]),
                    score=float(data["hand_score"][i, j]),
                )
                for j in range(int(data["hand_count"][i]))
            ]
            pose = PoseSample(np.array(data["pose"][i])) if data["pose_present"][i] else None
            features.append(FrameFeatures(int(ts), face, hands, pose))
    return features, plan
