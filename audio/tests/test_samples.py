import json
from pathlib import Path
from typing import TypedDict

import pytest

from audio.pipeline import AudioPipeline
from tests.conftest import spoken


class Sample(TypedDict):
    id: str
    passage: str
    heard: str
    expected: dict[str, int]


_SAMPLES: list[Sample] = json.loads((Path(__file__).parent / "fixtures" / "samples.json").read_text("utf-8"))


@pytest.mark.parametrize("sample", _SAMPLES, ids=[s["id"] for s in _SAMPLES])
def test_sample_passage_counts(lane: AudioPipeline, sample: Sample) -> None:
    """Answer-key check: hand-scored miscue counts for each sample reading."""
    result = lane.analyze(spoken(sample["heard"]), sample["passage"])
    counts: dict[str, object] = dict(result["score"]["counts"].items())
    counts["error_count"] = result["score"]["error_count"]
    got = {key: counts[key] for key in sample["expected"]}
    assert got == sample["expected"]
