from dataclasses import dataclass
from typing import Literal

from audio.alignment.text import is_hum, normalize
from audio.models import AlignedWord, SideEvent, SpokenWord, Token, new_row

_FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "hm", "mm", "mhm", "eh"})
_TRAILING_PUNCT = " .,!?;:\"')]"


@dataclass(frozen=True, slots=True)
class _Piece:
    norm: str
    raw: str
    start: float | None
    end: float | None
    cut: bool  # ASR marked it as broken off ("ox-")
    marker: bool = False  # CrisperWhisper non-speech tag ("[UM]", "[LAUGH]")


@dataclass(frozen=True, slots=True)
class CleanTranscript:
    """Alignable tokens + the noise removed from them (fillers, false starts)."""

    tokens: list[Token]
    side_events: list[SideEvent]


class TranscriptCleaner:
    """Raw ASR words -> alignable tokens. Rejoins split words, splits run-together names,
    drops fillers + false starts.
    """

    def __init__(self, max_join: int = 3) -> None:
        """max_join = most pieces merged into one passage word ("pan de sal") or passage words
        split out of one heard word ("Mangador" -> mang ador).
        """
        self._max_join = max_join

    def clean(self, words: list[SpokenWord], passage: list[str]) -> CleanTranscript:
        """Never drops a word that's in the passage, so real reading is never lost.

        A passage with a hum ('Mmm, Cheese!') keeps every hum-like sound (um, [UM], hmm) as a
        token: it may be the reading of that word. Ones the aligner doesn't pair with it become
        fillers again (alignment/detection.py).
        """
        vocab = frozenset(passage)
        keep_hums = any(is_hum(w) for w in passage)
        pieces = self._split_joined_words(self._join_split_words(self._expand(words), vocab), passage)
        tokens: list[Token] = []
        side: list[SideEvent] = []
        pending: list[AlignedWord] = []
        for i, piece in enumerate(pieces):
            nxt = pieces[i + 1].norm if i + 1 < len(pieces) else None
            noise = None if keep_hums and is_hum(piece.norm) else self._noise_kind(piece, nxt, vocab)
            if noise is not None:
                pending.append(new_row(noise, spoken=[piece.norm], start=piece.start, end=piece.end))
                continue
            side.extend(SideEvent(anchor=len(tokens), row=e) for e in pending)
            pending.clear()
            tokens.append(Token(len(tokens), piece.norm, piece.raw, piece.start, piece.end))
        side.extend(SideEvent(anchor=len(tokens), row=e) for e in pending)
        return CleanTranscript(tokens=tokens, side_events=side)

    def _expand(self, words: list[SpokenWord]) -> list[_Piece]:
        pieces: list[_Piece] = []
        for w in words:
            text = w["text"].strip()
            cut = text.rstrip(_TRAILING_PUNCT).endswith("-")
            marker = text.startswith("[") and text.rstrip(_TRAILING_PUNCT[:-1]).endswith("]")
            norms = normalize(text)
            for k, norm in enumerate(norms):
                last = k == len(norms) - 1
                pieces.append(_Piece(norm, text, w["start"], w["end"], cut and last, marker))
        return pieces

    def _join_split_words(self, pieces: list[_Piece], vocab: frozenset[str]) -> list[_Piece]:
        out: list[_Piece] = []
        i = 0
        while i < len(pieces):
            for n in range(self._max_join, 1, -1):
                group = pieces[i : i + n]
                joined = "".join(p.norm for p in group)
                if len(group) == n and joined in vocab and any(p.norm not in vocab for p in group):
                    raw = " ".join(p.raw for p in group)
                    out.append(_Piece(joined, raw, group[0].start, group[-1].end, False))
                    i += n
                    break
            else:
                out.append(pieces[i])
                i += 1
        return out

    def _split_joined_words(self, pieces: list[_Piece], passage: list[str]) -> list[_Piece]:
        """CrisperWhisper writes 'Mang Ador' as 'Mangador'. Split back if it's exactly 2+ adjacent passage
        words; near-joins ('Montenoy's') are the aligner's split move, which knows where the word sits.
        """
        vocab = frozenset(passage)
        compounds: dict[str, list[str]] = {}
        for n in range(2, self._max_join + 1):
            for i in range(len(passage) - n + 1):
                compounds.setdefault("".join(passage[i : i + n]), passage[i : i + n])
        out: list[_Piece] = []
        for piece in pieces:
            parts = compounds.get(piece.norm)
            if parts is None or piece.norm in vocab or piece.marker:
                out.append(piece)
                continue
            out.extend(self._share_timing(piece, parts))
        return out

    @staticmethod
    def _share_timing(piece: _Piece, parts: list[str]) -> list[_Piece]:
        """Splits the piece's time span across parts by letter count."""
        if piece.start is None or piece.end is None:
            return [_Piece(part, piece.raw, None, None, False) for part in parts]
        total = sum(len(part) for part in parts)
        span = piece.end - piece.start
        out: list[_Piece] = []
        t = piece.start
        for part in parts:
            nxt = t + span * len(part) / total
            out.append(_Piece(part, piece.raw, t, nxt, False))
            t = nxt
        return out

    def _noise_kind(
        self, piece: _Piece, nxt: str | None, vocab: frozenset[str]
    ) -> Literal["filler", "false_start"] | None:
        if piece.marker:
            return "filler"
        if piece.norm in vocab:
            return None
        if piece.norm in _FILLERS:
            return "filler"
        if piece.cut:
            return "false_start"
        if nxt is not None and len(piece.norm) < len(nxt) and nxt.startswith(piece.norm):
            return "false_start"  # "ox oxygen"
        return None
