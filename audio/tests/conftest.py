import json
from pathlib import Path

import pytest

from audio.types import LaneAResult, PreparedPassage

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> tuple[PreparedPassage, LaneAResult]:
    """Load tests/fixtures/lane_a/<name>.json as (PreparedPassage, LaneAResult)."""
    data = json.loads((FIXTURES / "lane_a" / f"{name}.json").read_text(encoding="utf-8"))
    return (
        PreparedPassage.model_validate(data["passage"]),
        LaneAResult.model_validate(data["lane_a"]),
    )


PASSAGES = FIXTURES / "passages"
PASSAGE_NAMES = (
    "a_mouse_and_a",
    "every_saturday",
    "mama_was",
    "marian_came",
    "nick_is_a_tri",
    "ring_ring",
)


def load_passage(name: str) -> str:
    """Load tests/fixtures/passages/<name>.txt (made-up placeholder text)."""
    return (PASSAGES / f"{name}.txt").read_text(encoding="utf-8")


class FakeG2P:
    """Deterministic G2P for tests without espeak: one segment per letter. Records its calls."""

    def __init__(self, table: dict[str, tuple[str, ...]] | None = None) -> None:
        self.table = table or {}
        self.calls: list[list[str]] = []

    def phonemize(self, words):
        self.calls.append(list(words))
        return [self.table.get(w) or tuple(c for c in w if c.isalnum()) for w in words]


@pytest.fixture(scope="session")
def espeak_g2p():
    """The real espeak G2P, or a skip when espeak-ng cannot be loaded."""
    from audio.phonetics.g2p import build_g2p
    from audio.settings import AudioSettings

    try:
        return build_g2p(AudioSettings())
    except Exception as exc:  # noqa: BLE001 - any failure to create the backend means no espeak
        pytest.skip(f"espeak-ng is not available: {exc}")


def pytest_collection_modifyitems(items):
    # Tests marked `espeak` request the `espeak_g2p` fixture, which skips when espeak is missing.
    for item in items:
        if "espeak" in item.keywords and "espeak_g2p" not in item.fixturenames:
            item.fixturenames.append("espeak_g2p")
