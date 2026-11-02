from typing import NamedTuple

from audio.alignment.text import is_hum, is_proper_noun, sound_key, word_distance
from audio.models import EventDetail, Tag

_ENDINGS = ("s", "es", "ed", "d", "ing")


class WordLabel(NamedTuple):
    """Pre-label for one passage word + optional reason."""

    tag: Tag
    detail: EventDetail | None


class WordLabeler:
    """Letter-based word labels. base() for alignment, label() for the derived pass.

    Ambiguous -> mispronunciation (conservative, per SRS). Lane B's arbiter may overrule
    with phoneme evidence; reversal is decided later in the post-pass.
    """

    def __init__(self, mispronunciation_below: float = 0.5) -> None:
        """Heard words closer than this (word_distance) are mispronunciations, not substitutions."""
        self._mispronunciation_below = mispronunciation_below

    def base(self, expected: str, heard: str, proper_nouns: frozenset[str]) -> WordLabel:
        """Alignment tag: match or substitution. Any hum reads a hum ('um' for 'Mmm').

        A name (or its possessive: 'Tinoy' covers "Tinoy's") matches only a SAME-SOUNDING spelling:
        the ASR doesn't know names ('Jackie' for 'Jacky'). A real misreading of a name counts like any
        other word (Phil-IRI has no name exception): 'Jack' -> mispronunciation, 'Bob' -> substitution.
        """
        if heard == expected or (is_hum(heard) and is_hum(expected)):
            return WordLabel("match", None)
        if is_proper_noun(expected, proper_nouns) and sound_key(heard) == sound_key(expected):
            return WordLabel("match", "proper_noun")
        return WordLabel("substitution", None)

    def label(self, expected: str, heard: str, proper_nouns: frozenset[str]) -> WordLabel:
        """base() + splits substitution into mispronunciation (close spelling / ending) vs substitution."""
        first = self.base(expected, heard, proper_nouns)
        if first.tag == "match":
            return first
        if self._differs_by_ending(expected, heard):
            return WordLabel("mispronunciation", "ending")
        if word_distance(expected, heard) < self._mispronunciation_below:
            return WordLabel("mispronunciation", None)
        return WordLabel("substitution", None)

    @staticmethod
    def _differs_by_ending(a: str, b: str) -> bool:
        short, long = sorted((a, b), key=len)
        return long.startswith(short) and long[len(short) :] in _ENDINGS
