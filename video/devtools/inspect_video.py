"""Print per-frame features of one webm as CSV, and the final decision as JSON on stderr.

Usage: uv run python -m devtools.inspect_video <webm> [--pose] [--overlay out.mp4]

--overlay draws landmarks onto the frames and writes an mp4 locally. Use it only with consented
clips and never share the output.
"""

import argparse
import contextlib
import csv
import dataclasses
import json
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import numpy as np

from app.analyzer import JobContext, SessionLike
from app.config import Settings
from app.decision import decide
from app.expression import EXPRESSION_GROUPS, _is_excluded
from app.frames import Frame, iter_frames, plan_sampling, probe
from app.landmarks import FrameFeatures, LandmarkerSession, ModelBundle
from app.pointing import arm_toward_screen, is_pointing_hand

COLUMNS = [
    "index",
    "timestamp_ms",
    "face",
    *EXPRESSION_GROUPS,
    "hands",
    "pointing_hand",
    "arm_toward_screen",
    "tip_x",
]


def _group_value(blendshapes: dict[str, float], members: tuple[str, ...]) -> object:
    members = tuple(m for m in members if not _is_excluded(m))
    values = [blendshapes[m] for m in members if m in blendshapes and np.isfinite(blendshapes[m])]
    return float(np.mean(values)) if values else ""


def frame_rows(features: list[FrameFeatures], settings: Settings) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, f in enumerate(features):
        row: dict[str, object] = {
            "index": i,
            "timestamp_ms": f.timestamp_ms,
            "face": int(f.face is not None),
        }
        for group, members in EXPRESSION_GROUPS.items():
            row[group] = "" if f.face is None else _group_value(f.face.blendshapes, members)
        row["hands"] = len(f.hands)
        row["pointing_hand"] = int(any(is_pointing_hand(h, settings) for h in f.hands))
        row["arm_toward_screen"] = int(
            settings.use_pose and f.pose is not None and arm_toward_screen(f.pose, settings)
        )
        row["tip_x"] = float(f.hands[0].image[8, 0]) if f.hands else ""
        rows.append(row)
    return rows


def _draw(frame: Frame, f: FrameFeatures) -> np.ndarray:
    import cv2

    image = np.ascontiguousarray(frame.rgb[:, :, ::-1])
    height, width = image.shape[:2]
    for hand in f.hands:
        for x, y, _ in hand.image:
            cv2.circle(image, (int(x * width), int(y * height)), 3, (0, 255, 0), -1)
    if f.pose is not None:
        for x, y, _, _ in f.pose.landmarks:
            cv2.circle(image, (int(x * width), int(y * height)), 3, (255, 0, 0), -1)
    return image


def main(
    argv: list[str] | None = None,
    session_factory: Callable[[], AbstractContextManager[SessionLike]] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("webm", type=Path)
    parser.add_argument("--pose", action="store_true")
    parser.add_argument("--overlay", type=Path)
    args = parser.parse_args(argv)

    settings = Settings(use_pose=args.pose)
    plan = plan_sampling(probe(args.webm, settings), settings)
    if session_factory is None:
        bundle = ModelBundle(settings)

        def session_factory() -> AbstractContextManager[SessionLike]:
            return LandmarkerSession(bundle, settings)

    ctx = JobContext("inspect", int(time.time() * 1000) + 365 * 24 * 3_600_000)
    features: list[FrameFeatures] = []
    writer = None
    with (
        session_factory() as session,
        contextlib.closing(iter_frames(args.webm, plan, ctx, settings)) as frames,
    ):
        for frame in frames:
            f = session.process(frame)
            features.append(f)
            if args.overlay is not None:
                import cv2

                if writer is None:
                    writer = cv2.VideoWriter(
                        str(args.overlay),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        plan.fps,
                        (plan.width, plan.height),
                    )
                writer.write(_draw(frame, f))
    if writer is not None:
        writer.release()

    out = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, lineterminator="\n")
    out.writeheader()
    out.writerows(frame_rows(features, settings))

    decision = decide(features, plan, settings)
    payload = {"flags": dataclasses.asdict(decision.flags), **decision.stats}
    print(json.dumps(payload, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
