from audio.phonetics.ipa import (
    INVENTORY,
    SPLIT_MAP,
    SYMBOL_MAP,
    normalise_segments,
    unknown_symbols,
)


def test_marks_are_stripped():
    assert normalise_segments(["ˈtʃ", "eɪ.", "ɹː"]) == ("tʃ", "eɪ", "ɹ")
    assert normalise_segments(["ˌk", "t͡ʃ", "d͜ʒ", "a‿", "iˑ"]) == ("k", "tʃ", "dʒ", "a", "i")


def test_symbol_map_is_applied_and_empty_segments_are_dropped():
    assert normalise_segments(["ᵻ", "ɐ", "g", "r", "ɝ"]) == ("ɪ", "ə", "ɡ", "ɹ", "ɜ")
    assert normalise_segments(["k", "ʔ", "ˈ", "ː", "t"]) == ("k", "t")
    assert SYMBOL_MAP["ʔ"] == ""


def test_normalisation_is_idempotent():
    once = normalise_segments(["ˈɡ", "ɹ", "eɪ", "t"])
    assert normalise_segments(once) == once


def test_unknown_symbol_is_kept_and_reported(caplog):
    seq = normalise_segments(["k", "ʘ", "t"])
    assert seq == ("k", "ʘ", "t")
    assert unknown_symbols(seq) == {"ʘ"}
    normalise_segments(["ʘ"])  # a second time: no second warning
    assert sum("ʘ" in r.getMessage() for r in caplog.records) <= 1


def test_inventory_contains_the_declared_units():
    for unit in ("tʃ", "dʒ", "eɪ", "aɪ", "ɔɪ", "aʊ", "oʊ", "ɚ", "ɜ", "n̩", "ɾ"):
        assert unit in INVENTORY


def test_merged_units_are_split():
    assert normalise_segments(["f", "ɔɹ"]) == ("f", "ɔ", "ɹ")
    assert normalise_segments(["t", "eɪ", "b", "əl"]) == ("t", "eɪ", "b", "ə", "l")
    assert normalise_segments(["f", "aɪɚ"]) == ("f", "aɪ", "ɚ")
    assert normalise_segments(["iə"]) == ("i", "ə")


def test_split_map_targets_are_in_the_inventory_and_merged_units_are_not():
    for merged, parts in SPLIT_MAP.items():
        assert merged not in INVENTORY
        assert set(parts) <= INVENTORY
    assert {"n̩", "eɪ", "aɪ", "ɔɪ", "aʊ", "oʊ", "tʃ", "dʒ"} <= INVENTORY


def test_greedy_fallback_splits_leftovers():
    assert normalise_segments(["oɹ"]) == ("o", "ɹ")
    assert normalise_segments(["tʃɹ"]) == ("tʃ", "ɹ")  # longest match first
    assert normalise_segments(["eɪl"]) == ("eɪ", "l")


def test_unsplittable_segment_is_kept(caplog):
    assert normalise_segments(["ʘ"]) == ("ʘ",)
    assert normalise_segments(["kʘ"]) == ("kʘ",)


def test_espeak_and_variant_spellings_of_for_agree():
    assert normalise_segments(["f", "ɔɹ"]) == normalise_segments("f ɔ ɹ".split())
