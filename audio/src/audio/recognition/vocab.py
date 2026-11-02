"""Model token -> normalised IPA segments, and CTC spans -> PhonemeTokens."""

from collections.abc import Iterable

from audio.phonetics.ipa import INVENTORY, normalise_segments
from audio.recognition.ctc import Span, frames_to_ms
from audio.types import PhonemeToken, PhoneSeq

DEFAULT_SPECIAL = frozenset({"<pad>", "<s>", "</s>", "<unk>", "|"})


def build_token_map(
    vocab: dict[str, int], special_tokens: Iterable[str] = ()
) -> dict[int, PhoneSeq]:
    """Map every vocabulary id to 0, 1 or several normalised segments. Specials map to ``()``."""
    special = DEFAULT_SPECIAL | set(special_tokens)
    return {
        idx: () if token in special else normalise_segments([token]) for token, idx in vocab.items()
    }


def unmapped_tokens(token_map: dict[int, PhoneSeq]) -> dict[int, tuple[str, ...]]:
    """Ids whose segments are outside INVENTORY (non-English phones), with those segments.

    They are kept, not dropped: B4 scores them through feature distance.
    """
    return {
        idx: tuple(s for s in seq if s not in INVENTORY)
        for idx, seq in token_map.items()
        if any(s not in INVENTORY for s in seq)
    }


def spans_to_tokens(
    spans: list[Span], token_map: dict[int, PhoneSeq], audio_ms: int
) -> tuple[PhonemeToken, ...]:
    """One PhonemeToken per segment. A span with k segments shares its frames equally, at least
    one frame each (so the last tokens may run past the span when it has fewer than k frames)."""
    out: list[PhonemeToken] = []
    for span in spans:
        segments = token_map.get(span.token_id, ())
        k = len(segments)
        if k == 0:
            continue
        n = span.end_frame - span.start_frame + 1
        share = max(1, n // k)
        for i, symbol in enumerate(segments):
            first = span.start_frame + i * share
            last = span.end_frame if i == k - 1 else first + share - 1
            last = max(last, first)
            start_ms, end_ms = frames_to_ms(first, last, audio_ms)
            out.append(
                PhonemeToken(
                    symbol=symbol,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=min(1.0, max(0.0, span.confidence)),
                )
            )
    out.sort(key=lambda t: (t.start_ms, t.end_ms))
    return tuple(out)
