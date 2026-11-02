from expression_helpers import block_step, make_frames, no_face_frames
from video_helpers import make_settings

from app.decision import decide
from app.expression import EXPRESSION_GROUPS
from app.frames import SamplePlan

PLAN = SamplePlan(fps=5.0, width=320, height=240, max_frames=900)


def test_decide_flags_flat_face_and_reports_stats():
    """Criterion 9."""
    decision = decide(make_frames(40), PLAN, make_settings())
    assert decision.flags.is_lack_expression is True
    assert decision.flags.is_finger_pointing is False
    assert decision.stats["frames"] == 40
    assert decision.stats["face_frames"] == 40
    assert decision.stats["hand_frames"] == 0
    assert decision.stats["face_coverage"] == 1.0
    assert decision.stats["expression_reason"] == "flat"
    for group in EXPRESSION_GROUPS:
        assert decision.stats[f"expr_var_{group}"] == 0.0


def test_decide_clears_flag_for_expressive_face():
    """Criterion 9."""
    decision = decide(block_step("smile", 0.4, count=60), PLAN, make_settings())
    assert decision.flags.is_lack_expression is False
    assert decision.flags.is_finger_pointing is False
    assert decision.stats["expression_reason"] == "expressive"
    assert decision.stats["expr_var_smile"] > 0.3


def test_decide_omits_variability_stats_when_face_is_insufficient():
    """Criterion 9."""
    features = make_frames(10) + no_face_frames(50)
    decision = decide(features, PLAN, make_settings())
    assert decision.flags.is_lack_expression is False
    assert decision.flags.is_finger_pointing is False
    assert decision.stats["expression_reason"] == "insufficient_face"
    assert decision.stats["frames"] == 60
    assert decision.stats["face_frames"] == 10
    assert "face_coverage" in decision.stats
    assert not any(key.startswith("expr_var_") for key in decision.stats)
