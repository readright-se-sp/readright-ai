from audio.models import AlignedItem, Column, Repeat, Swap, Token

_CLOSERS = " \"'”’)]"
_SENTENCE_END = (".", "!", "?")


class OffTextDetector:
    """Extra words before or after the reading, split off by a sentence break, are talk, not insertions.

    After:  '…I will. I think this good.'  -> 'I think this good' is off_text (not counted).
            '…would not be possible at all.' -> 'at all' continues the sentence: still insertions.
    Before: 'Okay, I'll read. A mouse and a cat…' -> 'okay i'll read' is off_text.
            'The a mouse and a cat…' -> 'the' runs into the reading: still an insertion.
    Uses the ASR's punctuation at the edge of the reading, never timing: how long the child
    waited before or after talking doesn't matter.
    """

    def mark(self, items: list[AlignedItem]) -> list[AlignedItem]:
        """Same items; insertions outside the read stretch become 'off' columns when a sentence breaks there."""
        read = [k for k, item in enumerate(items) if _read_token(item) is not None]
        if not read:
            return items
        first, last = read[0], read[-1]
        before = [k for k in range(first) if _is_ins(items[k])]
        after = [k for k in range(last + 1, len(items)) if _is_ins(items[k])]
        off: set[int] = set()
        if before and _ends_sentence(_ins_token(items[before[-1]])):
            off.update(before)
        if after and _ends_sentence(_read_token(items[last])):
            off.update(after)
        return [Column("off", None, item.h) if k in off and isinstance(item, Column) else item for k, item in enumerate(items)]


def _is_ins(item: AlignedItem) -> bool:
    return isinstance(item, Column) and item.op == "ins" and item.h is not None


def _ins_token(item: AlignedItem) -> Token | None:
    return item.h if isinstance(item, Column) else None


def _ends_sentence(token: Token | None) -> bool:
    return token is not None and token.raw.rstrip(_CLOSERS).endswith(_SENTENCE_END)


def _read_token(item: AlignedItem) -> Token | None:
    """The last heard token in this item that reads a passage word; None if it reads none."""
    match item:
        case Column(op="match" | "sub", p=int(), h=h) if h is not None:
            return h
        case Swap(heard=heard):
            return max(heard, key=lambda t: t.id)
        case Repeat(columns=columns):
            read = [c.h for c in columns if c.p is not None and c.h is not None]
            return max(read, key=lambda t: t.id) if read else None
        case _:
            return None
