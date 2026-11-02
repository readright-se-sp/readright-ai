from pointing_helpers import (
    arm_down_pose,
    hands_in_range,
    make_features,
    make_hand,
    resting_pose,
    sawtooth_xs,
)
from video_helpers import make_settings

from app.frames import SamplePlan
from app.pointing import assess_pointing

PLAN = SamplePlan(fps=5.0, width=640, height=360, max_frames=900)
STAT_KEYS = {
    "pointing_frames",
    "pointing_episodes",
    "pointing_total_ms",
    "sweeps",
    "pose_frames",
    "pointing_reason",
}


def pointing_for(count: int, total: int, **overrides):
    features = make_features(total, hands_in_range(10, 10 + count))
    return assess_pointing(features, PLAN, make_settings(**overrides))


def swept_features(periods: int, kind: str, score: float = 0.9):
    xs = sawtooth_xs(periods)
    return make_features(len(xs), lambda i: [make_hand(kind, score=score, tip_x=xs[i])])


# Criterion 5


def test_three_seconds_of_pointing_in_thirty_seconds_is_pointing():
    """Criterion 5."""
    result = pointing_for(15, 150)
    assert result.is_finger_pointing is True
    assert result.reason == "shape"
    assert result.stats["pointing_frames"] == 15
    assert result.stats["pointing_episodes"] == 1
    assert result.stats["pointing_total_ms"] == 3000.0


def test_one_second_of_pointing_in_thirty_seconds_is_not_pointing():
    """Criterion 5."""
    result = pointing_for(5, 150)
    assert result.is_finger_pointing is False
    assert result.reason == "none"


def test_long_recording_needs_the_fraction_of_recording_time():
    """Criterion 5."""
    # 120 s recording: 5% = 6000 ms = 30 frames. 29 frames is just under, 31 just over.
    assert pointing_for(29, 600).is_finger_pointing is False
    assert pointing_for(31, 600).is_finger_pointing is True


def test_total_threshold_follows_settings():
    """Criterion 5."""
    assert pointing_for(15, 150, pointing_min_total_ms=4000).is_finger_pointing is False
    assert pointing_for(15, 150, pointing_min_total_ms=2000).is_finger_pointing is True


# Criterion 6


def test_isolated_single_frame_detections_are_not_pointing():
    """Criterion 6."""
    hand = make_hand("pointing")
    features = make_features(150, lambda i: [hand] if i % 10 == 5 else [])
    result = assess_pointing(features, PLAN, make_settings())
    assert result.is_finger_pointing is False
    assert result.stats["pointing_frames"] == 15
    assert result.stats["pointing_episodes"] == 0
    assert result.stats["pointing_total_ms"] == 0.0
    assert result.reason == "none"


def test_short_gaps_inside_a_pointing_episode_are_bridged():
    """Criterion 4."""
    hand = make_hand("pointing")
    # 15 frames of pointing with one missed frame in the middle.
    features = make_features(60, lambda i: [hand] if 10 <= i < 25 and i != 17 else [])
    result = assess_pointing(features, PLAN, make_settings())
    assert result.is_finger_pointing is True
    assert result.stats["pointing_episodes"] == 1
    assert result.stats["pointing_total_ms"] == 3000.0
    assert result.stats["pointing_frames"] == 14


def test_non_pointing_shapes_never_count():
    """Criterion 2."""
    for kind in ("fist", "open", "tablet"):
        features = make_features(60, hands_in_range(0, 60, kind))
        result = assess_pointing(features, PLAN, make_settings(pointing_use_sweeps=False))
        assert result.is_finger_pointing is False
        assert result.reason == "none"
        assert result.stats["pointing_frames"] == 0


def test_weak_pointing_hand_does_not_count_as_shape():
    """Criterion 2."""
    hand = make_hand("pointing", score=0.3)
    features = make_features(60, lambda i: [hand])
    result = assess_pointing(features, PLAN, make_settings(pointing_use_sweeps=False))
    assert result.is_finger_pointing is False


# Criterion 8


def test_two_sweeps_without_pointing_shape_are_pointing_by_sweeps():
    """Criterion 8."""
    result = assess_pointing(swept_features(2, "open"), PLAN, make_settings())
    assert result.is_finger_pointing is True
    assert result.reason == "sweeps"
    assert result.stats["sweeps"] == 2
    assert result.stats["pointing_frames"] == 0


def test_same_sweeps_with_sweeps_disabled_are_not_pointing():
    """Criterion 8."""
    result = assess_pointing(
        swept_features(2, "open"), PLAN, make_settings(pointing_use_sweeps=False)
    )
    assert result.is_finger_pointing is False
    assert result.reason == "none"
    assert result.stats["sweeps"] == 0


def test_one_sweep_is_below_the_default_sweep_count():
    """Criterion 8."""
    result = assess_pointing(swept_features(1, "open"), PLAN, make_settings())
    assert result.is_finger_pointing is False
    assert result.stats["sweeps"] == 1
    assert result.reason == "none"
    lowered = assess_pointing(swept_features(1, "open"), PLAN, make_settings(sweep_min_count=1))
    assert lowered.is_finger_pointing is True


def test_sweeps_use_hands_of_any_score():
    """Criterion 8."""
    result = assess_pointing(swept_features(2, "open", score=0.1), PLAN, make_settings())
    assert result.is_finger_pointing is True
    assert result.reason == "sweeps"


def test_pointing_hand_that_also_sweeps_reports_both():
    """Criterion 8."""
    result = assess_pointing(swept_features(3, "pointing"), PLAN, make_settings())
    assert result.is_finger_pointing is True
    assert result.reason == "both"
    assert result.stats["sweeps"] == 3
    assert result.stats["pointing_episodes"] == 1


def test_hand_missing_frames_split_the_sweep_track():
    """Criterion 8."""
    xs = sawtooth_xs(2)
    features = make_features(
        len(xs), lambda i: [] if i % 11 == 5 else [make_hand("open", tip_x=xs[i])]
    )
    result = assess_pointing(features, PLAN, make_settings())
    assert result.stats["sweeps"] == 0
    assert result.is_finger_pointing is False


# Criterion 9


def test_pose_is_ignored_when_use_pose_is_off():
    """Criterion 9."""
    features = make_features(40, pose_at=lambda i: arm_down_pose())
    result = assess_pointing(features, PLAN, make_settings(use_pose=False))
    assert result.is_finger_pointing is False
    assert result.stats["pose_frames"] == 0
    assert result.reason == "no_hands"


def test_arm_toward_screen_without_hand_is_pointing_when_use_pose_is_on():
    """Criterion 9."""
    features = make_features(15, pose_at=lambda i: arm_down_pose())
    result = assess_pointing(features, PLAN, make_settings(use_pose=True))
    assert result.is_finger_pointing is True
    assert result.reason == "shape"
    assert result.stats["pose_frames"] == 15
    assert result.stats["pointing_frames"] == 15
    assert result.stats["pointing_total_ms"] == 3000.0


def test_arm_toward_screen_is_not_enough_when_too_brief():
    """Criterion 9."""
    features = make_features(30, pose_at=lambda i: arm_down_pose() if i < 5 else resting_pose())
    result = assess_pointing(features, PLAN, make_settings(use_pose=True))
    assert result.is_finger_pointing is False
    assert result.stats["pose_frames"] == 5


def test_arm_pose_does_not_count_while_a_non_pointing_hand_is_detected():
    """Criterion 9."""
    hand = make_hand("open")
    features = make_features(40, lambda i: [hand], lambda i: arm_down_pose())
    result = assess_pointing(
        features, PLAN, make_settings(use_pose=True, pointing_use_sweeps=False)
    )
    assert result.is_finger_pointing is False
    assert result.stats["pose_frames"] == 0
    assert result.reason == "none"


def test_pose_and_hand_evidence_combine_into_one_episode():
    """Criterion 9."""
    hand = make_hand("pointing")
    features = make_features(
        60,
        lambda i: [hand] if 10 <= i < 15 else [],
        lambda i: arm_down_pose() if 15 <= i < 25 else resting_pose(),
    )
    result = assess_pointing(features, PLAN, make_settings(use_pose=True))
    assert result.is_finger_pointing is True
    assert result.stats["pointing_episodes"] == 1
    assert result.stats["pointing_frames"] == 15
    assert result.stats["pose_frames"] == 10


# Criterion 10


def test_no_hands_and_no_pose_is_not_pointing():
    """Criterion 10."""
    result = assess_pointing(make_features(50), PLAN, make_settings())
    assert result.is_finger_pointing is False
    assert result.reason == "no_hands"
    assert set(result.stats) == STAT_KEYS
    assert result.stats["pointing_frames"] == 0
    assert result.stats["pointing_episodes"] == 0
    assert result.stats["pointing_total_ms"] == 0.0
    assert result.stats["sweeps"] == 0
    assert result.stats["pose_frames"] == 0
    assert result.stats["pointing_reason"] == "no_hands"


def test_resting_pose_with_use_pose_still_reports_no_hands():
    """Criterion 10."""
    features = make_features(50, pose_at=lambda i: resting_pose())
    result = assess_pointing(features, PLAN, make_settings(use_pose=True))
    assert result.is_finger_pointing is False
    assert result.reason == "no_hands"


def test_empty_recording_is_not_pointing():
    """Criterion 10."""
    result = assess_pointing([], PLAN, make_settings())
    assert result.is_finger_pointing is False
    assert result.reason == "no_hands"
