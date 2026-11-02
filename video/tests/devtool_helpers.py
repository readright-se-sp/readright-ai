"""Helpers for devtool tests: fake sessions, fake frames and feature comparison."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pointing_helpers import FRAME_STEP_MS, arm_down_pose, make_hand
from video_helpers import make_settings

from app.config import Settings
from app.frames import Frame, SamplePlan, VideoInfo
from app.landmarks import FaceSample, FrameFeatures, HandSample

FRAME_HEIGHT = 48
FRAME_WIDTH = 64
FRAME_COUNT = 6
PLAN = SamplePlan(fps=5.0, width=FRAME_WIDTH, height=FRAME_HEIGHT, max_frames=900)


def _hand(kind: str, handedness: str, score: float, index: int) -> HandSample:
    shift = index * 0.01
    base = make_hand(kind, score=score, tip_x=0.4 + shift)
    return replace(base, handedness=handedness, image=base.image + shift)


def sample_features(index: int) -> FrameFeatures:
    """Deterministic features for frame `index` covering every optional part.

    0: nothing. 1: face with a jaw blendshape. 2: partial face, one pointing hand, pose.
    3: two hands (pointing then fist), no face. 4: face with a different name set, open hand.
    5: pose only.
    """
    ts = index * FRAME_STEP_MS
    if index == 1:
        face = FaceSample({"mouthSmileLeft": 0.3, "mouthSmileRight": 0.4, "jawOpen": 0.1})
        return FrameFeatures(ts, face, [], None)
    if index == 2:
        hands = [_hand("pointing", "Right", 0.9, index)]
        return FrameFeatures(ts, FaceSample({"mouthSmileLeft": 0.5}), hands, arm_down_pose())
    if index == 3:
        hands = [_hand("pointing", "Left", 0.9, index), _hand("fist", "Right", 0.6, index)]
        return FrameFeatures(ts, None, hands, None)
    if index == 4:
        face = FaceSample({"browInnerUp": 0.2, "jawOpen": 0.7})
        return FrameFeatures(ts, face, [_hand("open", "Left", 0.8, index)], None)
    if index == 5:
        return FrameFeatures(ts, None, [], arm_down_pose())
    return FrameFeatures(ts, None, [], None)


class FakeSession:
    """Looks up features by frame index; never touches pixels."""

    def process(self, frame: Frame) -> FrameFeatures:
        return sample_features(frame.index)


def fake_session_factory() -> Any:
    @contextmanager
    def factory() -> Iterator[FakeSession]:
        yield FakeSession()

    return factory


def fake_frames(count: int = FRAME_COUNT) -> Iterator[Frame]:
    for i in range(count):
        yield Frame(i, i * FRAME_STEP_MS, np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), 7, np.uint8))


def patch_video_io(
    monkeypatch: pytest.MonkeyPatch, module: object, count: int = FRAME_COUNT
) -> None:
    """Replace probe, plan_sampling and iter_frames on a devtool module with fakes."""
    monkeypatch.setattr(module, "probe", lambda path, settings: VideoInfo(640, 480, 0, 1200))
    monkeypatch.setattr(module, "plan_sampling", lambda info, settings: PLAN)
    monkeypatch.setattr(module, "iter_frames", lambda path, plan, ctx, settings: fake_frames(count))


def make_webm(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"")
    return path


def default_settings(**overrides: Any) -> Settings:
    return make_settings(**overrides)


def assert_features_equal(actual: list[FrameFeatures], expected: list[FrameFeatures]) -> None:
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected, strict=True):
        assert got.timestamp_ms == want.timestamp_ms
        if want.face is None:
            assert got.face is None
        else:
            assert got.face is not None
            assert set(got.face.blendshapes) == set(want.face.blendshapes)
            assert got.face.blendshapes == pytest.approx(want.face.blendshapes)
        assert len(got.hands) == len(want.hands)
        for g_hand, w_hand in zip(got.hands, want.hands, strict=True):
            np.testing.assert_allclose(g_hand.world, w_hand.world)
            np.testing.assert_allclose(g_hand.image, w_hand.image)
            assert g_hand.handedness == w_hand.handedness
            assert g_hand.score == pytest.approx(w_hand.score)
        if want.pose is None:
            assert got.pose is None
        else:
            assert got.pose is not None
            np.testing.assert_allclose(got.pose.landmarks, want.pose.landmarks)
