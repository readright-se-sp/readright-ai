from dataclasses import dataclass
from typing import TYPE_CHECKING

from .analyzer import VideoFlags
from .config import Settings
from .expression import assess_expression
from .landmarks import FrameFeatures
from .pointing import assess_pointing

if TYPE_CHECKING:
    from .frames import SamplePlan


@dataclass(frozen=True)
class Decision:
    flags: VideoFlags
    stats: dict[str, float | int | str]


def decide(features: list[FrameFeatures], plan: "SamplePlan", settings: Settings) -> Decision:
    """Phase 6: lack of expression from face blendshapes, finger pointing from hand landmarks."""
    expression = assess_expression(features, settings)
    pointing = assess_pointing(features, plan, settings)
    stats: dict[str, float | int | str] = {
        "frames": len(features),
        "face_frames": expression.face_frames,
        "hand_frames": sum(bool(f.hands) for f in features),
        "face_coverage": expression.coverage,
        "expression_reason": expression.reason,
    }
    for group, value in expression.variability.items():
        stats[f"expr_var_{group}"] = value
    stats.update(pointing.stats)
    return Decision(
        flags=VideoFlags(
            is_finger_pointing=pointing.is_finger_pointing,
            is_lack_expression=expression.is_lack_expression,
        ),
        stats=stats,
    )
