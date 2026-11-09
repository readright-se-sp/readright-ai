"""Time the landmarkers on a recording and compare threads vs processes.

Usage: uv run python -m devtools.benchmark_frames <webm> [--parallel N] [--pose]
"""

import argparse
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import mediapipe as mp
import numpy as np

from app.analyzer import JobContext, MediaPipeAnalyzer
from app.config import Settings
from app.frames import iter_frames, plan_sampling, probe
from app.landmarks import LandmarkerSession, ModelBundle


def _ctx() -> JobContext:
    return JobContext("benchmark", int(time.time() * 1000) + 3_600_000)


def _run_job(webm: Path, use_pose: bool) -> float:
    analyzer = MediaPipeAnalyzer(Settings(use_pose=use_pose))
    analyzer.load()
    start = time.perf_counter()
    analyzer.analyze(webm, _ctx())
    return time.perf_counter() - start


def _per_landmarker(webm: Path, settings: Settings) -> None:
    plan = plan_sampling(probe(webm, settings), settings)
    frames = list(iter_frames(webm, plan, _ctx(), settings))
    with LandmarkerSession(ModelBundle(settings), settings) as session:
        parts = {"face": session._face, "hands": session._hands, "pose": session._pose}
        for name, landmarker in parts.items():
            if landmarker is None:
                continue
            times = []
            for frame in frames:
                data = np.ascontiguousarray(frame.rgb)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=data)
                start = time.perf_counter()
                landmarker.detect_for_video(image, frame.timestamp_ms)
                times.append((time.perf_counter() - start) * 1000)
            print(f"{name}: {sum(times) / len(times):.1f} ms/frame over {len(times)} frames")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("webm", type=Path)
    parser.add_argument("--parallel", type=int, default=0)
    parser.add_argument("--pose", action="store_true")
    args = parser.parse_args()

    _per_landmarker(args.webm, Settings(use_pose=args.pose))
    print(f"total job: {_run_job(args.webm, args.pose):.2f} s")

    if args.parallel > 0:
        pools = (("threads", ThreadPoolExecutor), ("processes", ProcessPoolExecutor))
        for label, pool_cls in pools:
            start = time.perf_counter()
            with pool_cls(max_workers=args.parallel) as pool:
                list(pool.map(_run_job, [args.webm] * args.parallel, [args.pose] * args.parallel))
            print(f"{args.parallel} jobs in {label}: {time.perf_counter() - start:.2f} s wall")


if __name__ == "__main__":
    main()
