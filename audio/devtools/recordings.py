"""Fixed mapping from a recording's file-name prefix to the passage it reads (B1.1)."""

from pathlib import Path

PASSAGES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "passages"

PASSAGE_NAME_BY_PREFIX = {
    "ringringra": "ring_ring",
    "mamawas": "mama_was",
    "everysatur": "every_saturday",
    "nickisatri": "nick_is_a_tri",
    "amouseanda": "a_mouse_and_a",
    "mariancame": "marian_came",
}

PASSAGE_BY_PREFIX: dict[str, str] = {
    prefix: (PASSAGES / f"{name}.txt").read_text(encoding="utf-8")
    for prefix, name in PASSAGE_NAME_BY_PREFIX.items()
}


def passage_for(wav_path: str | Path) -> str:
    """Passage text for a recording, by the longest matching file-name prefix."""
    name = Path(wav_path).name
    matches = [p for p in PASSAGE_BY_PREFIX if name.startswith(p)]
    if not matches:
        raise KeyError(f"no passage for recording {name!r}")
    return PASSAGE_BY_PREFIX[max(matches, key=len)]
