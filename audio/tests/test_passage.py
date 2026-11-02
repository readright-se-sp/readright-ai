import logging
import time

import pytest
from conftest import PASSAGE_NAMES, FakeG2P, load_passage

from audio.passage import (
    PassagePreparationError,
    PassagePreparer,
    interim_key,
    spoken_form,
    tokenise,
)
from audio.phonetics.g2p import G2PError
from audio.phonetics.ipa import INVENTORY
from audio.phonetics.lexicon import load_lexicon, load_variants

logger = logging.getLogger(__name__)


def test_tokenise_drops_punctuation_only_tokens():
    assert tokenise("Ring, ring! — it is ... a - cat") == ["Ring,", "ring!", "it", "is", "a", "cat"]
    assert tokenise("5 pesos") == ["5", "pesos"]


@pytest.mark.parametrize(
    ("raw", "spoken", "key"),
    [
        ("Don't", "don't", "dont"),
        ("Nick's,", "nick's", "nicks"),
        ("ice-cream.", "ice-cream", "icecream"),
        ("“Hello!”", "hello", "hello"),
        ("it’s", "it's", "its"),
    ],
)
def test_spoken_form_and_interim_key(raw, spoken, key):
    assert spoken_form(raw) == spoken
    assert interim_key(raw) == key


def test_token_indexes_are_consecutive():
    prepared = PassagePreparer(FakeG2P()).prepare("Hi — there, you!")
    assert [t.index for t in prepared.tokens] == [0, 1, 2]
    assert [t.raw for t in prepared.tokens] == ["Hi", "there,", "you!"]


def test_lexicon_word_bypasses_g2p():
    g2p = FakeG2P()
    prepared = PassagePreparer(g2p).prepare("Cebu cat")
    assert g2p.calls == [["cat"]]
    assert prepared.pronunciations["cebu"] == load_lexicon()["cebu"]


def test_variants_follow_espeak_and_are_deduplicated():
    g2p = FakeG2P({"the": ("ð", "ə"), "read": ("r", "i", "d")})
    prepared = PassagePreparer(g2p).prepare("the read")
    assert prepared.pronunciations["the"] == (("ð", "ə"), ("ð", "i"))  # "ð ə" once
    assert prepared.pronunciations["read"][0] == ("r", "i", "d")
    assert prepared.pronunciations["read"][1:] == load_variants()["read"]


def test_same_key_gives_union_of_pronunciations():
    g2p = FakeG2P({"nick's": ("a",), "nicks": ("b",)})
    prepared = PassagePreparer(g2p).prepare("Nick's nicks Nick's")
    assert {t.key for t in prepared.tokens} == {"nicks"}
    assert prepared.pronunciations["nicks"] == (("a",), ("b",))


def test_g2p_called_once_per_passage_and_cache_hit():
    g2p = FakeG2P()
    preparer = PassagePreparer(g2p, cache_size=4)
    first = preparer.prepare("a cat sat. A cat Cebu")
    assert g2p.calls == [["a", "cat", "sat"]]  # unique, non-lexicon, in one batch
    second = preparer.prepare("  a  cat\nsat. A cat   Cebu ")
    assert second is first
    assert len(g2p.calls) == 1
    assert first.passage_hash == second.passage_hash


def test_lru_eviction():
    g2p = FakeG2P()
    preparer = PassagePreparer(g2p, cache_size=2)
    preparer.prepare("one")
    preparer.prepare("two")
    preparer.prepare("one")  # refresh "one"; "two" is now the oldest
    preparer.prepare("three")  # evicts "two"
    assert len(g2p.calls) == 3
    preparer.prepare("one")
    assert len(g2p.calls) == 3  # still cached
    preparer.prepare("two")
    assert len(g2p.calls) == 4  # was evicted


def test_g2p_error_becomes_preparation_error_and_is_not_cached():
    class Failing:
        calls = 0

        def phonemize(self, words):
            self.calls += 1
            raise G2PError("zzz")

    g2p = Failing()
    preparer = PassagePreparer(g2p)
    for _ in range(2):
        with pytest.raises(PassagePreparationError) as info:
            preparer.prepare("zzz")
        assert info.value.token == "zzz"
    assert g2p.calls == 2  # the failure was not cached


def test_possessive_of_a_lexicon_word_takes_z_and_s_without_espeak():
    g2p = FakeG2P()
    prepared = PassagePreparer(g2p).prepare("Juaning’s Tinoy’s Tita’s")
    assert g2p.calls == []
    lexicon = load_lexicon()
    for key, base in [("juanings", "juaning"), ("tinoys", "tinoy"), ("titas", "tita")]:
        expected = [(*s, "z") for s in lexicon[base]] + [(*s, "s") for s in lexicon[base]]
        assert prepared.pronunciations[key] == tuple(expected)


def test_possessive_of_a_non_lexicon_word_still_goes_to_espeak():
    g2p = FakeG2P()
    PassagePreparer(g2p).prepare("Nick's Tita’s")
    assert g2p.calls == [["nick's"]]


def test_abbreviations_use_the_lexicon():
    g2p = FakeG2P()
    prepared = PassagePreparer(g2p).prepare("Mr. Perez and Mrs. Pardo, Mmm")
    assert g2p.calls == [["and"]]
    lexicon = load_lexicon()
    for key in ("mr", "mrs", "mmm"):
        assert prepared.pronunciations[key] == lexicon[key]
    assert lexicon["mr"] == (("m", "ɪ", "s", "t", "ɚ"),)


def test_new_filipino_entries_are_present():
    lexicon = load_lexicon()
    for word in ("mang", "aling", "ador", "taho", "mongo", "tita", "tinoy", "juaning", "manuel"):
        assert word in lexicon
    for word in ("perez", "pardo", "anansi", "marian", "tricycle"):
        assert word in lexicon


@pytest.mark.espeak
@pytest.mark.parametrize("name", PASSAGE_NAMES)
def test_real_passages_prepare(espeak_g2p, name):
    content = load_passage(name)
    preparer = PassagePreparer(espeak_g2p)
    prepared = preparer.prepare(content)
    assert 60 <= len(prepared.tokens) <= 120
    for token in prepared.tokens:
        variants = prepared.pronunciations[token.key]
        assert variants, token
        for seq in variants:
            assert set(seq) <= INVENTORY, (token.raw, seq)

    start = time.perf_counter()
    again = PassagePreparer(espeak_g2p).prepare(content)  # warm backend, empty cache
    elapsed = time.perf_counter() - start
    logger.info("warm prepare of %s took %.3f s", name, elapsed)
    print(f"warm prepare of {name}: {elapsed:.3f} s")
    assert elapsed < 1.0
    assert again.passage_hash == prepared.passage_hash


@pytest.mark.espeak
def test_inline_edge_cases_prepare(espeak_g2p):
    """Features the real texts lack: a digit, hyphens, a dash, contractions, heteronyms."""
    text = "I read 5 books. Don't live in an ice-cream shop — it's Nick's, isn't it? We live well."
    prepared = PassagePreparer(espeak_g2p).prepare(text)
    keys = {t.key for t in prepared.tokens}
    assert {"5", "dont", "icecream", "its", "nicks", "isnt"} <= keys
    assert len(prepared.tokens) == 17  # the dash is dropped
    assert len(prepared.pronunciations["read"]) >= 2
    assert len(prepared.pronunciations["live"]) >= 2
    for seqs in prepared.pronunciations.values():
        assert seqs
        for seq in seqs:
            assert set(seq) <= INVENTORY, seq


@pytest.mark.espeak
def test_passages_start_with_the_recording_prefix():
    starts = {
        "a_mouse_and_a": "A mouse and a",
        "anansi": "Anansi was tired",
        "every_saturday": "Every Saturday",
        "mama_was": "Mama was",
        "marian_came": "Marian came",
        "nick_is_a_tri": "Nick is a tri",
        "ring_ring": "“Ring! Ring!”",
    }
    for name, start in starts.items():
        assert load_passage(name).startswith(start)


ENGLISH_NAMES = {"jacky", "kris", "lisa", "mike", "nick", "marian", "mama", "mom", "saturday"}
ENGLISH_NAMES |= {"cheese", "i"}
_OPENING_QUOTES = ("“", '"')


def _mid_sentence_capitals(raws: list[str]) -> list[str]:
    """Spoken forms of capitalised tokens that are not at a sentence start."""
    found = []
    for i, raw in enumerate(raws):
        if i == 0 or raw.startswith(_OPENING_QUOTES):
            continue
        if raws[i - 1].rstrip("”’\"'").endswith((".", "!", "?")):
            continue
        letters = raw.lstrip("".join(_OPENING_QUOTES) + "‘'")
        if letters[:1].isupper():
            found.append(spoken_form(raw))
    return found


@pytest.mark.espeak
def test_real_passages_cover_every_word_and_name(espeak_g2p):
    lexicon = load_lexicon()
    preparer = PassagePreparer(espeak_g2p)
    anglicised = set()
    for name in PASSAGE_NAMES:
        content = load_passage(name)
        prepared = preparer.prepare(content)
        for key, seqs in prepared.pronunciations.items():
            assert seqs, (name, key)
            for seq in seqs:
                assert set(seq) <= INVENTORY, (name, key, seq)
        for form in _mid_sentence_capitals(tokenise(content)):
            base = form[:-2] if form.endswith("'s") else form
            if form not in lexicon and base not in lexicon:
                anglicised.add(form)
    unknown = anglicised - ENGLISH_NAMES
    assert not unknown, f"capitalised words that espeak would anglicise: {sorted(unknown)}"


def test_mid_sentence_capital_detector():
    raws = tokenise("The cat. “This is Juaning’s fish,” said Kris. Then, Mike went to Cebu!")
    assert _mid_sentence_capitals(raws) == ["juaning's", "kris", "mike", "cebu"]
