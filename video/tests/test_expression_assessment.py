import math

import pytest
from expression_helpers import BASELINE, block_step, make_frames, no_face_frames
from video_helpers import make_settings

from app.config import EXPRESSION_GROUP_NAMES
from app.expression import EXPRESSION_GROUPS, assess_expression
from app.landmarks import FaceSample, FrameFeatures

GROUPS = list(EXPRESSION_GROUPS)
DEFAULT_THRESHOLD = 0.12


def test_constant_face_is_flagged_flat():
    """Criterion 1."""
    result = assess_expression(make_frames(60), make_settings())
    assert result.is_lack_expression is True
    assert result.reason == "flat"


def test_smile_rising_and_falling_is_expressive():
    """Criterion 2."""

    def smile(i: int) -> dict[str, float]:
        if 20 <= i < 50:
            value = 0.05 + (0.6 - 0.05) * (i - 20) / 30
        elif 50 <= i < 80:
            value = 0.6 - (0.6 - 0.05) * (i - 50) / 30
        else:
            value = 0.05
        return {name: value for name in EXPRESSION_GROUPS["smile"]}

    result = assess_expression(make_frames(100, smile), make_settings())
    assert result.is_lack_expression is False
    assert result.reason == "expressive"


def test_changes_only_in_excluded_blendshapes_are_flagged_flat():
    """Criterion 3."""

    def excluded(i: int) -> dict[str, float]:
        swing = 0.9 if i % 10 < 5 else 0.0
        return {
            "jawOpen": swing,
            "mouthClose": swing,
            "eyeBlinkLeft": swing,
            "eyeLookInRight": swing,
            "mouthFunnel": swing,
            "cheekPuff": swing,
            "_neutral": swing,
        }

    result = assess_expression(make_frames(100, excluded), make_settings())
    assert result.is_lack_expression is True
    assert result.reason == "flat"


def test_single_frame_spike_is_removed_by_smoothing():
    """Criterion 4."""

    def spike(i: int) -> dict[str, float]:
        return {name: 0.9 for name in EXPRESSION_GROUPS["brow_raise"]} if i == 50 else {}

    settings = make_settings(expression_smooth_frames=3)
    result = assess_expression(make_frames(100, spike), settings)
    assert result.is_lack_expression is True
    assert result.reason == "flat"


def test_low_face_coverage_is_insufficient():
    """Criterion 5."""
    frames = make_frames(40) + no_face_frames(60)
    result = assess_expression(frames, make_settings(expression_min_face_coverage=0.5))
    assert result.is_lack_expression is False
    assert result.reason == "insufficient_face"
    assert result.variability == {}


def test_too_few_face_frames_is_insufficient():
    """Criterion 5."""
    result = assess_expression(make_frames(19), make_settings(expression_min_face_frames=20))
    assert result.is_lack_expression is False
    assert result.reason == "insufficient_face"
    assert result.variability == {}


def test_no_frames_is_insufficient():
    """Criterion 5."""
    result = assess_expression([], make_settings())
    assert result.is_lack_expression is False
    assert result.reason == "insufficient_face"
    assert result.coverage == 0.0
    assert result.face_frames == 0


@pytest.mark.parametrize("group", GROUPS)
def test_group_change_above_threshold_is_expressive(group):
    """Criterion 6."""
    frames = block_step(group, DEFAULT_THRESHOLD + 0.05)
    result = assess_expression(frames, make_settings())
    assert result.is_lack_expression is False
    assert result.reason == "expressive"


@pytest.mark.parametrize("group", GROUPS)
def test_group_change_below_threshold_is_flat(group):
    """Criterion 6."""
    frames = block_step(group, DEFAULT_THRESHOLD - 0.05)
    result = assess_expression(frames, make_settings())
    assert result.is_lack_expression is True
    assert result.reason == "flat"


def test_group_override_raises_only_that_groups_threshold():
    """Criterion 7."""
    settings = make_settings(expression_group_min_range={"smile": 0.5})
    assert assess_expression(block_step("smile", 0.3), settings).is_lack_expression is True
    assert assess_expression(block_step("frown", 0.3), settings).is_lack_expression is False


def test_group_value_is_mean_of_members_present():
    """Criterion 8."""
    frames = []
    for i in range(100):
        # One smile member everywhere, a second one only inside the middle block.
        shapes = {"mouthSmileRight": 0.1}
        if 30 <= i < 70:
            shapes["mouthSmileLeft"] = 0.5
        frames.append(FrameFeatures(i * 200, FaceSample(shapes), [], None))
    result = assess_expression(frames, make_settings())
    assert result.variability["smile"] == pytest.approx(0.2)


def test_frames_with_one_member_use_that_value():
    """Criterion 8."""
    frames = [
        FrameFeatures(i * 200, FaceSample({"mouthSmileLeft": BASELINE}), [], None)
        for i in range(60)
    ]
    result = assess_expression(frames, make_settings())
    assert result.is_lack_expression is True
    assert result.variability["smile"] == pytest.approx(0.0)
    assert result.variability["brow_raise"] == 0.0


def test_frames_with_one_member_still_detect_change():
    """Criterion 8."""
    frames = [
        FrameFeatures(
            i * 200, FaceSample({"mouthSmileLeft": 0.6 if 30 <= i < 70 else 0.1}), [], None
        )
        for i in range(100)
    ]
    result = assess_expression(frames, make_settings())
    assert result.is_lack_expression is False
    assert result.variability["smile"] == pytest.approx(0.5)


def test_non_finite_scores_are_ignored():
    """Criterion 8."""

    def bad(i: int) -> dict[str, float]:
        if i % 10 == 0:
            return {"mouthSmileLeft": float("nan"), "browInnerUp": float("inf")}
        return {}

    result = assess_expression(make_frames(100, bad), make_settings())
    assert result.is_lack_expression is True
    assert result.reason == "flat"
    assert all(math.isfinite(v) for v in result.variability.values())


def test_group_names_match_config():
    """Criterion 10."""
    assert set(EXPRESSION_GROUPS) == EXPRESSION_GROUP_NAMES
