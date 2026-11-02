"""Grapheme-to-phoneme via espeak-ng (phonemizer)."""

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from audio.phonetics.ipa import normalise_segments
from audio.types import PhoneSeq

if TYPE_CHECKING:
    from audio.settings import AudioSettings

logger = logging.getLogger(__name__)

WINDOWS_ESPEAK_LIBRARY = Path(r"C:\Program Files\eSpeak NG\libespeak-ng.dll")


class G2PError(Exception):
    def __init__(self, word: str) -> None:
        super().__init__(f"no pronunciation for {word!r}")
        self.word = word


class G2P(Protocol):
    def phonemize(self, words: Sequence[str]) -> list[PhoneSeq]:
        """Return one phoneme sequence per word, in the same order and length as ``words``."""
        ...


class EspeakG2P:
    """espeak-ng backend. Creating the backend is the expensive step, so it is created once."""

    def __init__(self, language: str = "en-us", library: Path | None = None) -> None:
        if library is not None:
            os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = str(library)
        # Imported late so PHONEMIZER_ESPEAK_LIBRARY is set before phonemizer reads it.
        from phonemizer.backend import EspeakBackend
        from phonemizer.separator import Separator

        self._separator = Separator(phone=" ", word="|", syllable="")
        self._backend = EspeakBackend(
            language,
            with_stress=False,
            preserve_punctuation=False,
            language_switch="remove-flags",
            words_mismatch="ignore",
        )

    def phonemize(self, words: Sequence[str]) -> list[PhoneSeq]:
        if not words:
            return []
        lines = self._backend.phonemize(list(words), separator=self._separator, strip=True, njobs=1)
        results: list[PhoneSeq] = []
        for word, line in zip(words, lines, strict=True):
            segments = [seg for chunk in line.split("|") for seg in chunk.split(" ")]
            seq = normalise_segments(segments)
            if not seq:
                raise G2PError(word)
            results.append(seq)
        return results


def build_g2p(settings: "AudioSettings") -> EspeakG2P:
    """The only place a G2P is built from settings."""
    library = settings.espeak_library
    if library is None and sys.platform == "win32" and WINDOWS_ESPEAK_LIBRARY.exists():
        library = WINDOWS_ESPEAK_LIBRARY
        logger.info("using the default espeak-ng library at %s", library)
    return EspeakG2P(settings.espeak_language, library)
