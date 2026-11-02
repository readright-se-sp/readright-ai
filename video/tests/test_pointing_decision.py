from dataclasses import replace

from expression_helpers import make_frames
from pointing_helpers import arm_down_pose, make_hand
from video_helpers import make_settings

from app.decision import decide
from app.frames import SamplePlan

PLAN = SamplePlan(fps=5.0, width=640, height=360, max_frames=900)

EXISTING_KEYS = {"frames", "face_frames", "hand_frames", "face_coverage", "expression_reason"}
POINTING_KEYS = {
    "pointing_frames",
    "pointing_episodes",
    "pointing_total_ms",
    "sweeps",
    "pose_frames",
    "pointing_reason",
}


def pointing_frames(count: int = 60, start: int = 10, stop: int = 25):
    hand = make_hand("pointing")
    return [
        replace(f, hands=[hand]) if start <= i < stop else f
        for i, f in enumerate(make_frames(count))
    ]


def test_decide_sets_both_flags_independently():
    """Criterion 11."""
    decision = decide(pointing_frames(), PLAN, make_settings())
    assert decision.flags.is_finger_pointing is True
    assert decision.flags.is_lack_expression is True


def test_decide_leaves_pointing_false_without_hands():
    """Criterion 11."""
    decision = decide(make_frames(60), PLAN, make_settings())
    assert decision.flags.is_finger_pointing is False
    assert decision.flags.is_lack_expression is True


def test_decide_keeps_existing_stats_and_adds_pointing_stats():
    """Criterion 11."""
    decision = decide(pointing_frames(), PLAN, make_settings())
    assert EXISTING_KEYS <= set(decision.stats)
    assert POINTING_KEYS <= set(decision.stats)
    assert decision.stats["frames"] == 60
    assert decision.stats["hand_frames"] == 15
    assert decision.stats["pointing_frames"] == 15
    assert decision.stats["pointing_episodes"] == 1
    assert decision.stats["pointing_total_ms"] == 3000.0
    assert decision.stats["sweeps"] == 0
    assert decision.stats["pose_frames"] == 0
    assert decision.stats["pointing_reason"] == "shape"


def test_decide_reports_pointing_stats_when_nothing_is_seen():
    """Criterion 11."""
    decision = decide(make_frames(40), PLAN, make_settings())
    assert POINTING_KEYS <= set(decision.stats)
    assert decision.stats["pointing_reason"] == "no_hands"
    assert decision.stats["pointing_frames"] == 0


def test_decide_uses_pose_only_when_enabled():
    """Criterion 11."""
    pose = arm_down_pose()
    frames = [replace(f, pose=pose) for f in make_frames(40)]
    off = decide(frames, PLAN, make_settings(use_pose=False))
    on = decide(frames, PLAN, make_settings(use_pose=True))
    assert off.flags.is_finger_pointing is False
    assert on.flags.is_finger_pointing is True
    assert on.stats["pose_frames"] == 40
