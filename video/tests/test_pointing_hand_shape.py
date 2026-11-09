import math

import numpy as np
import pytest
from pointing_helpers import (
    HAND_KINDS,
    ROTATIONS,
    STRAIGHT,
    arm_down_pose,
    make_hand,
    make_pose,
    resting_pose,
    transformed,
    with_bends,
)
from video_helpers import make_settings

from app.pointing import arm_toward_screen, bend_angles, is_pointing_hand

FINGER_NAMES = {"index", "middle", "ring", "pinky", "thumb"}


def test_bend_angles_names_all_five_fingers():
    """Criterion 1."""
    angles = bend_angles(make_hand("pointing").world)
    assert set(angles) == FINGER_NAMES


def test_bend_angles_are_zero_for_a_straight_finger():
    """Criterion 1."""
    angles = bend_angles(make_hand("pointing").world)
    assert angles["index"][0] == pytest.approx(0, abs=1e-6)
    assert angles["index"][1] == pytest.approx(0, abs=1e-6)


def test_bend_angles_are_ninety_for_a_right_angle_bend():
    """Criterion 1."""
    hand = with_bends({"index": (0, 90, 0), "middle": (0, 0, 90)})
    angles = bend_angles(hand.world)
    assert angles["index"] == pytest.approx((90, 0), abs=1e-6)
    assert angles["middle"] == pytest.approx((0, 90), abs=1e-6)


def test_bend_angles_are_nan_for_zero_length_bones():
    """Criterion 1."""
    angles = bend_angles(np.zeros((21, 3)))
    assert all(math.isnan(v) for pair in angles.values() for v in pair)


def test_pointing_hand_is_detected():
    """Criterion 2."""
    assert is_pointing_hand(make_hand("pointing"), make_settings()) is True


@pytest.mark.parametrize("kind", ["fist", "open", "tablet"])
def test_non_pointing_hand_shapes_are_rejected(kind):
    """Criterion 2."""
    assert is_pointing_hand(make_hand(kind), make_settings()) is False


def test_pointing_hand_below_min_score_is_rejected():
    """Criterion 2."""
    hand = make_hand("pointing", score=0.3)
    assert is_pointing_hand(hand, make_settings(pointing_min_hand_score=0.5)) is False
    assert is_pointing_hand(hand, make_settings(pointing_min_hand_score=0.2)) is True


def test_degenerate_hand_is_never_pointing():
    """Criterion 2."""
    hand = make_hand("pointing")
    flat = type(hand)(np.zeros((21, 3)), hand.image, hand.handedness, hand.score)
    assert is_pointing_hand(flat, make_settings()) is False


def test_only_one_curled_finger_is_not_pointing():
    """Criterion 2."""
    curled = (70, 100, 50)
    hand = with_bends({"index": STRAIGHT, "middle": curled, "ring": STRAIGHT, "pinky": STRAIGHT})
    assert is_pointing_hand(hand, make_settings()) is False


def test_two_curled_fingers_with_straight_index_is_pointing():
    """Criterion 2."""
    curled = (70, 100, 50)
    hand = with_bends({"index": STRAIGHT, "middle": curled, "ring": curled, "pinky": STRAIGHT})
    assert is_pointing_hand(hand, make_settings()) is True


@pytest.mark.parametrize("kind", HAND_KINDS)
@pytest.mark.parametrize(("rotation", "shift"), ROTATIONS)
def test_hand_shape_answer_is_unchanged_by_rotation_and_translation(kind, rotation, shift):
    """Criterion 3."""
    hand = make_hand(kind)
    settings = make_settings()
    expected = is_pointing_hand(hand, settings)
    assert expected is (kind == "pointing")
    assert is_pointing_hand(transformed(hand, rotation, shift), settings) is expected


def test_arm_toward_screen_accepts_a_low_central_wrist_with_lower_elbow():
    """Criterion 9."""
    assert arm_toward_screen(arm_down_pose(), make_settings()) is True


def test_arm_toward_screen_works_for_the_right_side():
    """Criterion 9."""
    pose = make_pose(right=(0.55, 0.85, 1.0, 0.95))
    assert arm_toward_screen(pose, make_settings()) is True


def test_arm_toward_screen_ignores_use_pose_setting():
    """Criterion 9."""
    assert arm_toward_screen(arm_down_pose(), make_settings(use_pose=False)) is True


def test_resting_pose_is_not_arm_toward_screen():
    """Criterion 9."""
    assert arm_toward_screen(resting_pose(), make_settings()) is False


@pytest.mark.parametrize(
    "side",
    [
        (0.45, 0.85, 0.2, 0.95),  # wrist barely visible
        (0.45, 0.60, 1.0, 0.95),  # wrist above the bottom band (y < 0.7)
        (0.05, 0.85, 1.0, 0.95),  # wrist far left of both shoulders (> 0.1 outside)
        (0.95, 0.85, 1.0, 0.95),  # wrist far right of both shoulders
        (0.45, 0.85, 1.0, 0.75),  # elbow higher than the wrist
    ],
)
def test_arm_toward_screen_rejects_each_failed_condition(side):
    """Criterion 9."""
    assert arm_toward_screen(make_pose(left=side), make_settings()) is False


def test_arm_toward_screen_honours_visibility_and_band_settings():
    """Criterion 9."""
    pose = make_pose(left=(0.45, 0.85, 0.6, 0.95))
    assert arm_toward_screen(pose, make_settings(pointing_pose_min_visibility=0.5)) is True
    assert arm_toward_screen(pose, make_settings(pointing_pose_min_visibility=0.8)) is False
    assert arm_toward_screen(pose, make_settings(pointing_pose_bottom_band=0.1)) is False
