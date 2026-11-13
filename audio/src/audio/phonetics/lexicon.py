"""Loads the hand-written pronunciation data files (``data/lexicon.yaml``, ``data/variants.yaml``).

Both map a lowercase spoken form to a tuple of pronunciations. Each pronunciation is written as a
string of space-separated IPA segments and passes through ``normalise_segments`` on load.
"""

from functools import cache
from importlib import resources

import yaml

from audio.phonetics.ipa import normalise_segments
from audio.types import PhoneSeq

Pronunciations = dict[str, tuple[PhoneSeq, ...]]


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _load(filename: str) -> Pronunciations:
    text = (resources.files("audio.phonetics") / "data" / filename).read_text(encoding="utf-8")
    raw = yaml.load(text, Loader=_UniqueKeyLoader)  # SafeLoader subclass
    result: Pronunciations = {}
    for key, pronunciations in raw.items():
        if not isinstance(key, str) or key != key.lower():
            raise ValueError(f"{filename}: key {key!r} must be a lowercase string")
        if not pronunciations:
            raise ValueError(f"{filename}: {key!r} has no pronunciations")
        seqs = tuple(normalise_segments(p.split()) for p in pronunciations)
        if not all(seqs):
            raise ValueError(f"{filename}: {key!r} has an empty pronunciation")
        result[key] = seqs
    return result


@cache
def load_lexicon() -> Pronunciations:
    """Filipino proper nouns and loanwords. An entry replaces espeak for that word."""
    return _load("lexicon.yaml")


@cache
def load_variants() -> Pronunciations:
    """Pronunciations added to espeak's own (function words, heteronyms)."""
    return _load("variants.yaml")
