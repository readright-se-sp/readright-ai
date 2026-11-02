"""Stage 0: passage preparation.

``PassagePreparer.prepare(content)`` tokenises the passage with Lane A's ``normalize`` (so token
positions equal Lane A's passage indices), takes each token's normalised word as its key and gives
every key its accepted IPA pronunciations (lexicon, else espeak plus variants).
"""

import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict

from audio.alignment.text import normalize
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


def passage_words(content: str) -> list[tuple[str, str, str]]:
    """``(raw, key, spoken)`` for each passage position, in Lane A's ``tokenize_passage`` order.

    ``key`` is Lane A's normalised word. ``raw`` is the whitespace token it came from (shared by
    the parts of ``ice-cream`` or ``21``). ``spoken`` is the G2P input: the raw token's spoken form
    (``don't``, ``nick's``) when the token is one word, else the normalised word.
    """
    out: list[tuple[str, str, str]] = []
    for raw in content.split():
        words = normalize(raw)
        if len(words) == 1:
            form = spoken_form(raw)
            out.append((raw, words[0], words[0] if form.isdigit() else form))
        else:
            out.extend((raw, w, w) for w in words)
    return out


def spoken_form(raw: str) -> str:
    """G2P input and lexicon/variant lookup key: lowercase, outer punctuation stripped."""
    text = raw.lower().replace("’", "'")
    start, end = 0, len(text)
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return text[start:end]


def _possessive_base(form: str, lexicon) -> str | None:
    """The lexicon word of a possessive (``juaning's`` -> ``juaning``), else None."""
    if form.endswith("'s") and form[:-2] in lexicon:
        return form[:-2]
    return None


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

        prepared = self._build(passage_hash, content)

        if self._cache_size > 0:
            with self._lock:
                self._cache[passage_hash] = prepared
                self._cache.move_to_end(passage_hash)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return prepared

    def _build(self, passage_hash: str, content: str) -> PreparedPassage:
        words = passage_words(content)
        lexicon, variants = load_lexicon(), load_variants()

        spoken = [sp for _, _, sp in words]
        unique = list(dict.fromkeys(spoken))
        # Lookup order: lexicon, possessive of a lexicon word, espeak.
        to_phonemize = [
            w for w in unique if w not in lexicon and _possessive_base(w, lexicon) is None
        ]
        try:
            phonemized = self._g2p.phonemize(to_phonemize) if to_phonemize else []
        except G2PError as exc:
            raise PassagePreparationError(exc.word) from exc
        espeak = dict(zip(to_phonemize, phonemized, strict=True))

        per_form: dict[str, list[PhoneSeq]] = {}
        for form in unique:
            base = _possessive_base(form, lexicon)
            if form in lexicon:
                seqs = list(lexicon[form])
            elif base is not None:
                # ponytail: no ɪz after s/z/ʃ/ʒ/tʃ/dʒ; add when a passage has such a name.
                seqs = [(*s, "z") for s in lexicon[base]] + [(*s, "s") for s in lexicon[base]]
            else:
                seqs = [espeak[form], *variants.get(form, ())]
            per_form[form] = _dedupe(seqs)

        tokens: list[PassageToken] = []
        by_key: dict[str, list[PhoneSeq]] = {}
        for i, ((raw, key, _), form) in enumerate(zip(words, spoken, strict=True)):
            tokens.append(PassageToken(index=i, raw=raw, key=key))
            by_key.setdefault(key, []).extend(per_form[form])

        pronunciations = {key: tuple(_dedupe(seqs)) for key, seqs in by_key.items()}
        return PreparedPassage(
            passage_hash=passage_hash, tokens=tuple(tokens), pronunciations=pronunciations
        )
