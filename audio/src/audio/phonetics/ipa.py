"""IPA segment normalisation.

Every phoneme sequence in the lane (espeak output, lexicon, variants, and later the recogniser
output) passes through :func:`normalise_segments`.
"""

import logging
import unicodedata
from collections.abc import Iterable

from audio.types import PhoneSeq

logger = logging.getLogger(__name__)

# Removed outright: stress, syllable mark, tie bars, linking mark, length marks.
_STRIPPED = frozenset("ˈˌ.͜͡‿ːˑ")

# Declared as a dict so later phases (B2) can extend it. An empty value drops the segment.
SYMBOL_MAP: dict[str, str] = {
    "ᵻ": "ɪ",
    "ɐ": "ə",
    "g": "ɡ",  # ASCII g -> IPA script g
    "r": "ɹ",
    "ɝ": "ɜ",
    "ʔ": "",
}

# Allowed segments: everything the en-us espeak voice produces after normalisation, plus the
# Tagalog tap. Built from the placeholder passages, the lexicon, the variants and a few hundred
# common words, then frozen and reviewed by hand.
INVENTORY: frozenset[str] = frozenset(
    {
        # consonants
        *"bdfhjklmnpstvwz",
        "ð",
        "ŋ",
        "ɡ",
        "ɹ",
        "ʃ",
        "ʒ",
        "θ",
        "tʃ",
        "dʒ",
        "ɾ",  # Tagalog tap (lexicon); espeak also emits it for intervocalic t/d
        "n̩",  # syllabic n
        # vowels
        *"aiou",  # a, o and u also occur in the lexicon's five-vowel Filipino forms
        "æ",
        "ɑ",
        "ɔ",
        "ə",
        "ɛ",
        "ɜ",
        "ɪ",
        "ʊ",
        "ʌ",
        "ɚ",
        # diphthongs and espeak's merged vowel+r / vowel+l units
        "eɪ",
        "aɪ",
        "ɔɪ",
        "aʊ",
        "oʊ",
        "aɪə",
        "aɪɚ",
        "iə",
        "ɑɹ",
        "ɔɹ",
        "ɛɹ",
        "ɪɹ",
        "ʊɹ",
        "əl",
    }
)

_warned: set[str] = set()


def _normalise_one(segment: str) -> str:
    text = unicodedata.normalize("NFD", segment)
    text = "".join(c for c in text if c not in _STRIPPED)
    text = SYMBOL_MAP.get(text, text)
    return unicodedata.normalize("NFC", text)


def normalise_segments(segments: Iterable[str]) -> PhoneSeq:
    """Normalise IPA segments: strip marks, map symbols, drop segments that become empty."""
    out = [n for n in (_normalise_one(s) for s in segments) if n]
    for seg in out:
        if seg not in INVENTORY and seg not in _warned:
            _warned.add(seg)
            logger.warning("phoneme %r is not in the IPA inventory", seg)
    return tuple(out)


def unknown_symbols(seq: Iterable[str]) -> set[str]:
    """Segments of ``seq`` that are not in :data:`INVENTORY`."""
    return {s for s in seq if s not in INVENTORY}
