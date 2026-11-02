import json
from pathlib import Path

from audio.types import LaneAResult, PreparedPassage

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> tuple[PreparedPassage, LaneAResult]:
    """Load tests/fixtures/lane_a/<name>.json as (PreparedPassage, LaneAResult)."""
    data = json.loads((FIXTURES / "lane_a" / f"{name}.json").read_text(encoding="utf-8"))
    return (
        PreparedPassage.model_validate(data["passage"]),
        LaneAResult.model_validate(data["lane_a"]),
    )
