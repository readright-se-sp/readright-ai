import pytest

from audio.phonetics.ipa import INVENTORY
from audio.phonetics.lexicon import load_lexicon, load_variants


@pytest.mark.parametrize("loader", [load_lexicon, load_variants])
def test_files_load_and_are_well_formed(loader):
    data = loader()
    assert data
    for key, variants in data.items():
        assert key == key.lower()
        assert variants, key
        assert all(variants), key


@pytest.mark.parametrize("loader", [load_lexicon, load_variants])
def test_every_segment_is_in_the_inventory(loader):
    for key, variants in loader().items():
        for seq in variants:
            unknown = set(seq) - INVENTORY
            assert not unknown, f"{key}: {unknown}"


def test_lexicon_size_and_order():
    lexicon = load_lexicon()
    assert 30 <= len(lexicon) <= 60
    assert lexicon["cebu"][0] == ("s", "ɛ", "b", "u")  # Filipino form first
    assert lexicon["cebu"][1] == ("s", "eɪ", "b", "u")  # anglicised second
    assert len(lexicon["jeepney"]) == 1


def test_variants_hold_both_readings():
    variants = load_variants()
    assert len(variants) == 20
    assert ("ð", "ə") in variants["the"] and ("ð", "i") in variants["the"]
