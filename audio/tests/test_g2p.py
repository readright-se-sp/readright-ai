import sys
from pathlib import Path

import pytest
import yaml

from audio.config import AudioSettings
from audio.phonetics import g2p as g2p_module
from audio.phonetics.g2p import EspeakG2P, G2PError, build_g2p
from audio.phonetics.ipa import INVENTORY

GOLDEN = Path(__file__).parent / "fixtures" / "g2p_golden.yaml"


espeak_only = pytest.mark.espeak


@espeak_only
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


@espeak_only
def test_chair_is_split_into_single_segments(espeak_g2p):
    assert espeak_g2p.phonemize(["chair"])[0] == ("tʃ", "ɛ", "ɹ")


@espeak_only
def test_output_length_and_digits(espeak_g2p):
    words = ["cat", "5", "Mr."]
    out = espeak_g2p.phonemize(words)
    assert len(out) == len(words)
    assert all(out)


@espeak_only
@pytest.mark.parametrize("word", ["jacky", "kris", "lisa", "mike", "nick", "you're", "nick's"])
def test_english_names_and_contractions_have_known_symbols(espeak_g2p, word):
    seq = espeak_g2p.phonemize([word])[0]
    assert seq
    assert set(seq) <= INVENTORY, (word, seq)


@espeak_only
def test_empty_input(espeak_g2p):
    assert espeak_g2p.phonemize([]) == []


def test_g2p_error_carries_the_word():
    assert G2PError("xyz").word == "xyz"


@espeak_only
def test_golden_words(espeak_g2p):
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    words = list(golden["words"])
    for word, seq in zip(words, espeak_g2p.phonemize(words), strict=True):
        assert " ".join(seq) == golden["words"][word], word


@espeak_only
def test_is_espeak_g2p_instance(espeak_g2p):
    assert isinstance(espeak_g2p, EspeakG2P)


@espeak_only
def test_espeak_and_variants_spellings_of_for_dedupe(espeak_g2p):
    from audio.passage import PassagePreparer

    variants = PassagePreparer(espeak_g2p).prepare("for").pronunciations["for"]
    assert variants.count(("f", "ɔ", "ɹ")) == 1
    assert len(set(variants)) == len(variants)


class _FakeEspeak:
    calls: list = []

    def __init__(self, language, library=None):
        type(self).calls.append((language, library))


@pytest.fixture
def fake_backend(monkeypatch):
    _FakeEspeak.calls = []
    monkeypatch.setattr(g2p_module, "EspeakG2P", _FakeEspeak)
    return _FakeEspeak


def test_build_g2p_forwards_the_setting(fake_backend, tmp_path):
    lib = tmp_path / "libespeak-ng.so"
    build_g2p(AudioSettings(ESPEAK_LIBRARY=lib, ESPEAK_LANGUAGE="en-gb", _env_file=None))
    assert fake_backend.calls == [("en-gb", lib)]


def test_windows_default_applies_only_when_unset(fake_backend, monkeypatch, tmp_path):
    default = tmp_path / "default.dll"
    default.write_bytes(b"")
    monkeypatch.setattr(g2p_module, "WINDOWS_ESPEAK_LIBRARY", default)
    monkeypatch.setattr(sys, "platform", "win32")
    build_g2p(AudioSettings(_env_file=None))
    explicit = tmp_path / "explicit.dll"
    build_g2p(AudioSettings(ESPEAK_LIBRARY=explicit, _env_file=None))
    monkeypatch.setattr(sys, "platform", "linux")
    build_g2p(AudioSettings(_env_file=None))
    assert [c[1] for c in fake_backend.calls] == [default, explicit, None]
