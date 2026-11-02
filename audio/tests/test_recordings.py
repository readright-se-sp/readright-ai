import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "devtools"))
from recordings import PASSAGE_BY_PREFIX, passage_for  # noqa: E402


def test_every_prefix_maps_to_its_passage():
    assert passage_for("ringringraf85a.wav").startswith("“Ring! Ring!”")
    assert passage_for(Path("x/everysatur88f3.wav")).startswith("Every Saturday")
    assert len(PASSAGE_BY_PREFIX) == 6


def test_unknown_recording_raises():
    with pytest.raises(KeyError):
        passage_for("unknown.wav")
