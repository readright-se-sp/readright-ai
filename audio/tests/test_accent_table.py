import pytest

from audio.phonetics.accent_table import load_accent_table
from audio.phonetics.ipa import INVENTORY, VOWELS

SPEC_PAIRS = [("f", "p"), ("v", "b"), ("θ", "t"), ("ð", "d"), ("z", "s"), ("ʒ", "ʃ")]


def test_vowels_in_inventory():
    assert VOWELS <= INVENTORY
    assert "n̩" not in VOWELS


def test_real_yaml_loads():
    table = load_accent_table()
    assert set(SPEC_PAIRS) <= table.pairs
    assert table.costs["accent"] == 0.1


GOOD = """
costs: {accent: 0.1, final_cluster_drop: 0.2, substitution_floor: 0.4, indel: 1.0}
pairs:
  - [f, p]
"""


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("[f, p]", "[☃, p]"),  # unknown expected symbol
        GOOD.replace("indel: 1.0", "indel: 1.5"),  # cost out of range
        GOOD.replace(", indel: 1.0", ""),  # missing cost
        GOOD + "  - [f, p]\n",  # duplicate pair
    ],
)
def test_loader_rejects(tmp_path, text):
    path = tmp_path / "t.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_accent_table(path)


def test_loader_accepts_good(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(GOOD, encoding="utf-8")
    assert load_accent_table(path).pairs == {("f", "p")}
