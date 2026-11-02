import itertools

from audio.phonetics.features import feature_distance, load_table
from audio.phonetics.ipa import INVENTORY


def test_table_covers_inventory():
    table = load_table()
    assert INVENTORY <= set(table["symbols"])
    assert table["scale_R"] > 0


def test_identity_symmetry_range():
    for a in INVENTORY:
        assert feature_distance(a, a) == 0
    for a, b in itertools.combinations(sorted(INVENTORY), 2):
        d = feature_distance(a, b)
        assert d == feature_distance(b, a)
        assert 0 <= d <= 1


def test_sanity():
    assert feature_distance("p", "b") < feature_distance("p", "a")


def test_unknown_symbol():
    assert feature_distance("☃", "p") == 1.0
    assert feature_distance("p", "☃") == 1.0
