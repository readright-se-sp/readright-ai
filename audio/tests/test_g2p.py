from pathlib import Path

import pytest
import yaml

from audio.phonetics.g2p import EspeakG2P, G2PError

pytestmark = pytest.mark.espeak

GOLDEN = Path(__file__).parent / "fixtures" / "g2p_golden.yaml"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("cat", "k æ t"),
        ("fish", "f ɪ ʃ"),
        ("ship", "ʃ ɪ p"),
        ("thin", "θ ɪ n"),
        ("this", "ð ɪ s"),
        ("jump", "dʒ ʌ m p"),
        ("sing", "s ɪ ŋ"),
        ("day", "d eɪ"),  # the diphthong is one segment
    ],
)
def test_hard_assertions(espeak_g2p, word, expected):
    assert espeak_g2p.phonemize([word])[0] == tuple(expected.split())


def test_chair_starts_with_tsh(espeak_g2p):
    assert espeak_g2p.phonemize(["chair"])[0][0] == "tʃ"


def test_output_length_and_digits(espeak_g2p):
    words = ["cat", "5", "Mr."]
    out = espeak_g2p.phonemize(words)
    assert len(out) == len(words)
    assert all(out)


def test_empty_input(espeak_g2p):
    assert espeak_g2p.phonemize([]) == []


def test_g2p_error_carries_the_word():
    assert G2PError("xyz").word == "xyz"


def test_golden_words(espeak_g2p):
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    words = list(golden["words"])
    for word, seq in zip(words, espeak_g2p.phonemize(words), strict=True):
        assert " ".join(seq) == golden["words"][word], word


def test_is_espeak_g2p_instance(espeak_g2p):
    assert isinstance(espeak_g2p, EspeakG2P)
