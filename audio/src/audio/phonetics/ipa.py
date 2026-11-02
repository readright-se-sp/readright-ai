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

# Declared as a dict so later phases can extend it. An empty value drops the segment.
SYMBOL_MAP: dict[str, str] = {
    "ᵻ": "ɪ",
    "ɐ": "ə",
    "g": "ɡ",  # ASCII g -> IPA script g
    "r": "ɹ",
    "ɝ": "ɜ",
    "ʔ": "",
}

# espeak's merged units, split into single segments so every source (espeak, lexicon, variants,
# recogniser) uses the same segmentation. Applied after SYMBOL_MAP.
SPLIT_MAP: dict[str, tuple[str, ...]] = {
    "ɑɹ": ("ɑ", "ɹ"),
    "ɔɹ": ("ɔ", "ɹ"),
    "ɛɹ": ("ɛ", "ɹ"),
    "ɪɹ": ("ɪ", "ɹ"),
    "ʊɹ": ("ʊ", "ɹ"),
    "əl": ("ə", "l"),
    "aɪə": ("aɪ", "ə"),
    "aɪɚ": ("aɪ", "ɚ"),
    "iə": ("i", "ə"),
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
        # diphthongs (espeak's merged vowel+r / vowel+l units are split, see SPLIT_MAP)
        "eɪ",
        "aɪ",
        "ɔɪ",
        "aʊ",
        "oʊ",
    }
)

_warned: set[str] = set()


def _normalise_one(segment: str) -> str:
    text = unicodedata.normalize("NFD", segment)
    text = "".join(c for c in text if c not in _STRIPPED)
    text = SYMBOL_MAP.get(text, text)
    return unicodedata.normalize("NFC", text)


def _split_greedy(segment: str) -> tuple[str, ...] | None:
    """Split into INVENTORY segments, longest match first; None when that is not possible."""
    parts: list[str] = []
    pos = 0
    longest = max(len(s) for s in INVENTORY)
    while pos < len(segment):
        for size in range(min(longest, len(segment) - pos), 0, -1):
            if segment[pos : pos + size] in INVENTORY:
                parts.append(segment[pos : pos + size])
                pos += size
                break
        else:
            return None
    return tuple(parts)


def _expand(segment: str) -> tuple[str, ...]:
    if segment in SPLIT_MAP:  # step 5
        return SPLIT_MAP[segment]
    if segment not in INVENTORY:  # step 6
        parts = _split_greedy(segment)
        if parts is not None and len(parts) > 1:
            return parts
    return (segment,)


def normalise_segments(segments: Iterable[str]) -> PhoneSeq:
    """Normalise IPA segments: strip marks, map symbols, split merged units, drop empty ones."""
    out = [part for s in segments if (n := _normalise_one(s)) for part in _expand(n)]
    for seg in out:
        if seg not in INVENTORY and seg not in _warned:
            _warned.add(seg)
            logger.warning("phoneme %r is not in the IPA inventory", seg)
    return tuple(out)


def unknown_symbols(seq: Iterable[str]) -> set[str]:
    """Segments of ``seq`` that are not in :data:`INVENTORY`."""
    return {s for s in seq if s not in INVENTORY}
