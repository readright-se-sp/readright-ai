import pytest
from conftest import FakeG2P

from audio.lane_b.compare import compare, hypothesis_variants, unit_variants
from audio.passage import PassagePreparer
from audio.phonetics.accent_table import load_accent_table
from audio.phonetics.g2p import G2PError
from audio.types import PassageToken, PreparedPassage, Unit

TABLE = load_accent_table()
ACCENT = TABLE.costs["accent"]
FLOOR = TABLE.costs["substitution_floor"]
CLUSTER = TABLE.costs["final_cluster_drop"]
INDEL = TABLE.costs["indel"]


def best(heard, *variants):
    result = compare(heard, variants)
    assert result is not None
    return result


def test_identical():
    r = best(["k", "æ", "t"], ("k", "æ", "t"))
    assert r.distance == 0 and r.deviations == ()


@pytest.mark.parametrize(("x", "y"), sorted(TABLE.pairs))
def test_accent_pairs(x, y):
    r = best(["k", y, "t"], ("k", x, "t"))
    assert r.distance == pytest.approx(ACCENT / 3)
    assert r.deviations == ()


def test_pair_is_one_way():
    r = best(["k", "f", "t"], ("k", "p", "t"))
    assert r.deviations == (("p", "f"),)
    assert r.distance > ACCENT / 3


def test_cat_cap():
    r = best(["k", "æ", "p"], ("k", "æ", "t"))
    assert r.deviations == (("t", "p"),)
    assert r.distance >= FLOOR / 3


def test_fink_think():
    r = best(["f", "ɪ", "ŋ", "k"], ("θ", "ɪ", "ŋ", "k"))
    assert r.deviations == (("θ", "f"),)
    assert r.distance > ACCENT / 4


def test_final_cluster_and_indels():
    r = best(["t", "ɛ", "s"], ("t", "ɛ", "s", "t"))
    assert r.distance == pytest.approx(CLUSTER / 4) and r.deviations == ()
    r = best(["k", "æ"], ("k", "æ", "t"))
    assert r.distance == pytest.approx(INDEL / 3) and r.deviations == (("t", ""),)
    r = best(["k", "t", "ɛ"], ("k", "s", "t", "ɛ"))
    assert r.distance == pytest.approx(INDEL / 4)


def test_insertion_and_deletion_sides():
    assert best(["k", "æ", "t"], ("k", "æ")).deviations == (("", "t"),)
    assert best(["k"], ("k", "æ")).deviations == (("æ", ""),)


def test_best_variant():
    heard = ["k", "æ", "t"]
    assert best(heard, ("k", "æ", "p"), ("k", "æ", "t")).best_variant_index == 1
    assert best(heard, ("k", "æ", "t"), ("k", "æ", "t")).best_variant_index == 0


def test_empty_heard():
    assert best([], ("t", "ɛ", "s", "t")).distance == pytest.approx((3 * INDEL + CLUSTER) / 4)
    assert best([], ("k", "æ", "t")).distance == pytest.approx(INDEL)


def test_no_variant():
    assert compare(["a"], []) is None
    assert compare(["a"], [()]) is None


def test_tie_break_deterministic():
    a = best(["a"], ("a", "a"))
    assert a.deviations == (("a", ""),)
    assert best(["a"], ("a", "a")) == a


def _passage():
    keys = ["x", "y", "z"]
    return PreparedPassage(
        passage_hash="h",
        tokens=tuple(PassageToken(index=i, raw=k, key=k) for i, k in enumerate(keys)),
        pronunciations={
            "x": (("a",), ("b",)),
            "y": (("c",), ("d",)),
            "z": tuple((str(i),) for i in range(5)),
        },
    )


def _unit(surface="x", idx=0, span=1):
    return Unit(
        surface=surface,
        key=surface,
        passage_index=idx,
        passage_span=span if idx is not None else 0,
        source_indices=(0,),
        start_ms=0,
        end_ms=100,
        align_score=1.0,
    )


def test_unit_variants():
    p = _passage()
    assert unit_variants(_unit(idx=None), p) == ()
    assert unit_variants(_unit(span=2), p) == (("a", "c"), ("a", "d"), ("b", "c"), ("b", "d"))
    assert unit_variants(_unit(span=1), p) == (("a",), ("b",))
    big = PreparedPassage(
        passage_hash="h",
        tokens=tuple(PassageToken(index=i, raw="z", key="z") for i in range(3)),
        pronunciations={"z": tuple((str(i),) for i in range(5))},
    )
    assert len(unit_variants(_unit(idx=0, span=3), big)) == 16


class RaisingG2P:
    def phonemize(self, words):
        raise G2PError(words[0])


def test_hypothesis_variants():
    g2p = FakeG2P()
    preparer = PassagePreparer(g2p, cache_size=0)
    units = [_unit("Aling"), _unit(""), _unit("cat")]
    result = hypothesis_variants(units, preparer)
    assert result[0] == (("a", "l", "i", "ŋ"), ("æ", "l", "ɪ", "ŋ"))  # lexicon, not G2P
    assert result[1] == ()
    assert result[2] == (("c", "a", "t"),)
    assert g2p.calls == [["cat"]]


def test_hypothesis_variants_g2p_error():
    units = [_unit("cat"), _unit("dog")]
    assert hypothesis_variants(units, PassagePreparer(RaisingG2P(), cache_size=0)) == ((), ())
