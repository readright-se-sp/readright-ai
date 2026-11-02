from dataclasses import dataclass
from typing import TYPE_CHECKING

from .analyzer import VideoFlags
from .config import Settings
from .expression import assess_expression
from .landmarks import FrameFeatures

if TYPE_CHECKING:
    from .frames import SamplePlan


@dataclass(frozen=True)
class Decision:
    flags: VideoFlags
    stats: dict[str, float | int | str]


def decide(features: list[FrameFeatures], plan: "SamplePlan", settings: Settings) -> Decision:
    """Phase 5: lack of expression from face blendshapes; finger pointing is Phase 6 (false)."""
    expression = assess_expression(features, settings)
    stats: dict[str, float | int | str] = {
        "frames": len(features),
        "face_frames": expression.face_frames,
        "hand_frames": sum(bool(f.hands) for f in features),
        "face_coverage": expression.coverage,
        "expression_reason": expression.reason,
    }
    for group, value in expression.variability.items():
        stats[f"expr_var_{group}"] = value
    return Decision(
        flags=VideoFlags(
            is_finger_pointing=False, is_lack_expression=expression.is_lack_expression
        ),
        stats=stats,
    )
