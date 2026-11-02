"""Stage 0: passage preparation.

``PassagePreparer.prepare(content)`` tokenises the passage, assigns each token a matching key and
gives every key its accepted IPA pronunciations (lexicon, else espeak plus variants).
"""

import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict

from audio.phonetics.g2p import G2P, G2PError
from audio.phonetics.lexicon import load_lexicon, load_variants
from audio.types import PassageToken, PhoneSeq, PreparedPassage


class PassagePreparationError(Exception):
    def __init__(self, token: str) -> None:
        super().__init__(f"cannot prepare a pronunciation for {token!r}")
        self.token = token


_WHITESPACE = re.compile(r"\s+")


def _normalise_text(content: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", content)).strip()


def tokenise(content: str) -> list[str]:
    """Split on whitespace; drop tokens with no letter or digit (``—``, ``-``, ``...``)."""
    text = unicodedata.normalize("NFC", content)
    return [t for t in text.split() if any(c.isalnum() for c in t)]


def spoken_form(raw: str) -> str:
    """G2P input and lexicon/variant lookup key: lowercase, outer punctuation stripped."""
    text = raw.lower().replace("’", "'")
    start, end = 0, len(text)
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return text[start:end]


def interim_key(raw: str) -> str:
    """Matching key: ``spoken_form`` without apostrophes and hyphens (``dont``, ``icecream``).

    Interim stand-in for Lane A's Normalise (Rev 2 rules 1, 4 and 7 only). Number words,
    contractions, possessives and homophones (rules 2, 3, 5, 6, 8) are Lane A's job. This is the
    only function to replace when the two normalisers are reconciled.
    """
    return spoken_form(raw).replace("'", "").replace("-", "")


def _dedupe(seqs: list[PhoneSeq]) -> list[PhoneSeq]:
    return list(dict.fromkeys(seqs))


class PassagePreparer:
    def __init__(self, g2p: G2P, cache_size: int = 128) -> None:
        self._g2p = g2p
        self._cache_size = cache_size
        self._cache: OrderedDict[str, PreparedPassage] = OrderedDict()
        self._lock = threading.Lock()

    def prepare(self, content: str) -> PreparedPassage:
        text = _normalise_text(content)
        passage_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            cached = self._cache.get(passage_hash)
            if cached is not None:
                self._cache.move_to_end(passage_hash)
                return cached

        prepared = self._build(passage_hash, text)

        if self._cache_size > 0:
            with self._lock:
                self._cache[passage_hash] = prepared
                self._cache.move_to_end(passage_hash)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return prepared

    def _build(self, passage_hash: str, text: str) -> PreparedPassage:
        raws = tokenise(text)
        lexicon, variants = load_lexicon(), load_variants()

        spoken = [spoken_form(r) for r in raws]
        unique = list(dict.fromkeys(spoken))
        to_phonemize = [w for w in unique if w not in lexicon]
        try:
            phonemized = self._g2p.phonemize(to_phonemize) if to_phonemize else []
        except G2PError as exc:
            raise PassagePreparationError(exc.word) from exc
        espeak = dict(zip(to_phonemize, phonemized, strict=True))

        per_form: dict[str, list[PhoneSeq]] = {}
        for form in unique:
            if form in lexicon:
                seqs = list(lexicon[form])
            else:
                seqs = [espeak[form], *variants.get(form, ())]
            per_form[form] = _dedupe(seqs)

        tokens: list[PassageToken] = []
        by_key: dict[str, list[PhoneSeq]] = {}
        for i, (raw, form) in enumerate(zip(raws, spoken, strict=True)):
            key = interim_key(raw)
            tokens.append(PassageToken(index=i, raw=raw, key=key))
            by_key.setdefault(key, []).extend(per_form[form])

        pronunciations = {key: tuple(_dedupe(seqs)) for key, seqs in by_key.items()}
        return PreparedPassage(
            passage_hash=passage_hash, tokens=tuple(tokens), pronunciations=pronunciations
        )
