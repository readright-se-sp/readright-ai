from dataclasses import dataclass
from typing import TYPE_CHECKING

from .analyzer import VideoFlags
from .config import Settings
from .landmarks import FrameFeatures

if TYPE_CHECKING:
    from .frames import SamplePlan


@dataclass(frozen=True)
class Decision:
    flags: VideoFlags
    stats: dict[str, float | int | str]


def decide(features: list[FrameFeatures], plan: "SamplePlan", settings: Settings) -> Decision:
    """Phase 3: no decision logic yet (Phases 5-6); both flags are false."""
    return Decision(
        flags=VideoFlags(is_finger_pointing=False, is_lack_expression=False),
        stats={
            "frames": len(features),
            "face_frames": sum(f.face is not None for f in features),
            "hand_frames": sum(bool(f.hands) for f in features),
        },
    )
