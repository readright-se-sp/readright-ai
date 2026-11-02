"""Cache landmark features from webms so thresholds can be tuned without re-running MediaPipe.

Usage: uv run python -m devtools.extract_features <webm_dir> [--cache DIR] [--pose]

Writes one <stem>.npz per webm (landmarks only, no image data). Local use with consented clips.
"""

import argparse
import contextlib
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from app.analyzer import JobContext, SessionLike
from app.config import Settings
from app.frames import iter_frames, plan_sampling, probe
from app.landmarks import FrameFeatures, LandmarkerSession, ModelBundle
from devtools.feature_cache import load_cache, save_cache

SessionFactory = Callable[[], AbstractContextManager[SessionLike]]


def _ctx() -> JobContext:
    return JobContext("extract", int(time.time() * 1000) + 365 * 24 * 3_600_000)


def extract_file(
    webm: Path,
    cache_dir: Path,
    settings: Settings,
    session_factory: SessionFactory | None = None,
) -> Path:
    plan = plan_sampling(probe(webm, settings), settings)
    if session_factory is None:
        bundle = ModelBundle(settings)

        def session_factory() -> AbstractContextManager[SessionLike]:
            return LandmarkerSession(bundle, settings)

    features: list[FrameFeatures] = []
    ctx = _ctx()
    with (
        session_factory() as session,
        contextlib.closing(iter_frames(webm, plan, ctx, settings)) as frames,
    ):
        for frame in frames:
            features.append(session.process(frame))
    out = cache_dir / f"{webm.stem}.npz"
    save_cache(out, features, plan)
    return out


def main(argv: list[str] | None = None, session_factory: SessionFactory | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("webm_dir", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".feature-cache"))
    parser.add_argument("--pose", action="store_true")
    args = parser.parse_args(argv)

    if not args.webm_dir.is_dir():
        print(f"not a directory: {args.webm_dir}")
        return 1
    webms = sorted(args.webm_dir.glob("*.webm"))
    if not webms:
        print(f"no .webm files in {args.webm_dir}")
        return 1

    settings = Settings(use_pose=args.pose)
    args.cache.mkdir(parents=True, exist_ok=True)
    failed = 0
    for webm in webms:
        try:
            out = extract_file(webm, args.cache, settings, session_factory)
        except Exception as err:
            failed += 1
            (args.cache / f"{webm.stem}.npz").unlink(missing_ok=True)
            print(f"{webm.name}: failed ({type(err).__name__}: {err})")
            continue
        features, _ = load_cache(out)
        faces = sum(f.face is not None for f in features)
        hands = sum(bool(f.hands) for f in features)
        print(f"{webm.name}: {len(features)} frames, {faces} with face, {hands} with hands")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
