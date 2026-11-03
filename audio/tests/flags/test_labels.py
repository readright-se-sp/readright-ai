import csv
from pathlib import Path

from devtools.recordings import PASSAGE_NAME_BY_PREFIX

LABELS = Path(__file__).resolve().parents[1] / "fixtures" / "flags_labels.csv"
COLUMNS = [
    "recording",
    "word_by_word",
    "disregard_punctuation",
    "monotonous",
    "hardly_audible",
    "notes",
]


def test_flags_labels_are_well_formed() -> None:
    with LABELS.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        rows = list(reader)
    assert len(rows) == 9
    assert len({r["recording"] for r in rows}) == 9
    for r in rows:
        assert r["recording"].startswith(tuple(PASSAGE_NAME_BY_PREFIX)), r["recording"]
        for flag in COLUMNS[1:5]:
            assert r[flag] in {"yes", "no"}, (r["recording"], flag)
