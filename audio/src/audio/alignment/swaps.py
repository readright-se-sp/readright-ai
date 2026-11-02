from audio.alignment.text import word_distance
from audio.models import AlignedItem, Column, Repeat, Swap


class SwapDetector:
    """Packages NW transposition columns as Swaps: swp mid* swp (neighbours have no mid).

    NW decides the transposition (needleman_wunsch.py); this only groups its columns. A half
    split off by a later stage (move put-back) is read as an ordinary pairing.
    An earlier copy said just before the swap ('happy wos happy was') is folded in: NW's re-read
    state only re-reads in passage order, so it can't see a swapped re-read. Words said the same
    -> one repetition; changed words -> the swap's tries. Last reading wins, as in repeats.py.
    """

    def __init__(self, similar_below: float = 0.5) -> None:
        """similar_below = word_distance under which an earlier word counts as a try at the swap's word."""
        self._similar_below = similar_below

    def detect(self, items: list[AlignedItem], passage: list[str]) -> list[AlignedItem]:
        """Same items, with every swp group replaced by a Swap. Repeats pass through."""
        out: list[AlignedItem] = []
        i = 0
        while i < len(items):
            item = items[i]
            if not isinstance(item, Column) or item.op not in ("swp", "mid"):
                out.append(item)
                i += 1
                continue
            found = self._swap_at(items, i, passage)
            if found is None:
                out.append(_plain(item, passage))
                i += 1
            else:
                out.extend(self._fold_earlier_copy(out, found[0], passage))
                i += found[1]
        return out

    @staticmethod
    def _swap_at(items: list[AlignedItem], i: int, passage: list[str]) -> tuple[Swap, int] | None:
        a = _column(items, i)
        if a is None or a.op != "swp" or a.p is None or a.h is None:
            return None
        k = i + 1
        while (col := _column(items, k)) is not None and col.op == "mid":
            k += 1
        b = _column(items, k)
        if b is None or b.op != "swp" or b.p is None or b.h is None or b.p >= a.p:
            return None
        mids = tuple(_plain(c, passage) for c in items[i + 1 : k] if isinstance(c, Column))
        return Swap(b.p, a.p, (a.h, b.h), mids), k - i + 1

    def _fold_earlier_copy(self, out: list[AlignedItem], swap: Swap, passage: list[str]) -> list[AlignedItem]:
        """Pops insertions before the swap that match its spoken words in order (a prefix is enough)."""
        later, earlier = swap.heard
        said = [(swap.q, later), *((c.p, c.h) for c in swap.mids if c.p is not None and c.h is not None), (swap.p, earlier)]
        k = len(out)
        while k > 0 and len(out) - k < len(said) and isinstance(col := out[k - 1], Column) and col.op == "ins":
            k -= 1
        copy = [c.h for c in out[k:] if isinstance(c, Column) and c.h is not None]
        while copy and not all(self._similar(t.norm, h.norm) for t, (_, h) in zip(copy, said)):
            copy.pop(0)  # keep the longest run that lines up with the start of the swap
            k += 1
        if not copy:
            return [swap]
        del out[k:]
        same = tuple(t for t, (_, h) in zip(copy, said) if t.norm == h.norm)
        tries = tuple((p, (t.norm,)) for t, (p, h) in zip(copy, said) if t.norm != h.norm)
        folded = Swap(swap.p, swap.q, swap.heard, swap.mids, tries)
        return [Repeat(same, (), ()), folded] if same else [folded]

    def _similar(self, a: str, b: str) -> bool:
        return a == b or word_distance(a, b) < self._similar_below


def _column(items: list[AlignedItem], k: int) -> Column | None:
    item = items[k] if k < len(items) else None
    return item if isinstance(item, Column) else None


def _plain(col: Column, passage: list[str]) -> Column:
    """swp/mid column as the ordinary pairing it is on its own."""
    if col.p is None or col.h is None:
        return col
    return Column("match" if col.h.norm == passage[col.p] else "sub", col.p, col.h)
