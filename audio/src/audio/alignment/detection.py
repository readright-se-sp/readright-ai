from audio.alignment.labels import WordLabeler
from audio.alignment.text import is_hum
from audio.models import AlignedItem, AlignedWord, Column, Repeat, SideEvent, Swap, Token, new_row, retag


class Detector:
    """Lane A step 4: NW items -> rows with base tags, in reading order."""

    def __init__(self, labeler: WordLabeler) -> None:
        """Injects the per-word labeler."""
        self._labeler = labeler

    def detect(
        self,
        items: list[AlignedItem],
        side_events: list[SideEvent],
        passage: list[str],
        proper_nouns: frozenset[str],
    ) -> list[AlignedWord]:
        """One row per passage word + insertions; side rows slotted in. Swaps get move_id 1, 2, ..."""
        pending = sorted(side_events, key=lambda s: s.anchor)
        rows: list[AlignedWord] = []
        swaps = 0
        for item in items:
            if isinstance(item, Swap):
                swaps += 1
                self._slot(rows, pending, item.heard[0].id)
                rows.extend(self._swap_rows(item, passage, proper_nouns, swaps))
            elif isinstance(item, Repeat):
                if item.heard:
                    self._slot(rows, pending, item.heard[0].id)
                    rows.append(self._repetition_row(item))
                for col, tries in zip(item.columns, item.tries):
                    self._slot(rows, pending, None if col.h is None else col.h.id)
                    rows.append(self._row(col, passage, proper_nouns, tries))
            else:
                self._slot(rows, pending, None if item.h is None else item.h.id)
                rows.append(self._row(item, passage, proper_nouns))
        rows.extend(s.row for s in pending)
        if any(is_hum(w) for w in passage):
            rows = [retag(r, "filler") if r["tag"] == "insertion" and _is_hum_row(r) else r for r in rows]
        return rows

    @staticmethod
    def _slot(rows: list[AlignedWord], pending: list[SideEvent], first: int | None) -> None:
        """Side rows anchored at or before token `first` go in before it."""
        while first is not None and pending and pending[0].anchor <= first:
            rows.append(pending.pop(0).row)

    def _swap_rows(self, swap: Swap, passage: list[str], nouns: frozenset[str], move_id: int) -> list[AlignedWord]:
        """'was happy' read 'happy was' -> was<-was, happy<-happy (own timings), first row counted.

        A misread half ('happy wos') is its own miscue: that row is tagged substitution (the post-pass
        makes it mispronunciation), keeps the move_id, and the other half carries the swap — 2 errors.
        NW never lets both halves be misread. Words between the halves stay ordinary rows.
        Earlier tries ('happy wos happy was') go in attempts; the post-pass judges them.
        """
        later, earlier = swap.heard
        tries = dict(swap.tries)
        halves = [(swap.p, earlier), (swap.q, later)]
        misread = [self._labeler.base(passage[p], h.norm, nouns).tag != "match" for p, h in halves]
        swap_at = misread.index(False) if any(misread) else 0
        rows = [
            self._swap_half(p, h, passage, move_id, tries.get(p, ()), misread[k], swap_at == k)
            for k, (p, h) in enumerate(halves)
        ]
        rows[1:1] = [self._row(c, passage, nouns, tries.get(c.p, ()) if c.p is not None else ()) for c in swap.mids]
        return rows

    @staticmethod
    def _swap_half(
        p: int, h: Token, passage: list[str], move_id: int, tries: tuple[str, ...], misread: bool, carries_swap: bool
    ) -> AlignedWord:
        return new_row(
            "substitution" if misread else "swap", passage_indices=[p], passage_words=[passage[p]],
            spoken=[h.norm], attempts=list(tries), start=h.start, end=h.end,
            move_id=move_id, counted=misread or carries_swap,
        )

    @staticmethod
    def _repetition_row(repeat: Repeat) -> AlignedWord:
        """All earlier copies in one row: 'they can' said 3 times -> spoken 'they can they can', one error."""
        heard = repeat.heard
        return new_row("repetition", spoken=[t.norm for t in heard], start=heard[0].start, end=heard[-1].end)

    def _row(
        self, item: Column, passage: list[str], nouns: frozenset[str], tries: tuple[str, ...] = ()
    ) -> AlignedWord:
        """tries = earlier different readings (retry, re-read): kept in attempts, judged in the post-pass."""
        match item:
            case Column(op="del", p=int(p)):
                return new_row("omission", passage_indices=[p], passage_words=[passage[p]])
            case Column(op="ins", h=h) if h is not None:
                return new_row("insertion", spoken=[h.norm], start=h.start, end=h.end)
            case Column(op="off", h=h) if h is not None:
                return new_row("off_text", spoken=[h.norm], start=h.start, end=h.end)
            case Column(op="match" | "sub", p=int(p), h=h) if h is not None:
                tag, detail = self._labeler.base(passage[p], h.norm, nouns)
                return new_row(
                    tag, passage_indices=[p], passage_words=[passage[p]], spoken=[h.norm],
                    attempts=list(tries), detail=detail, start=h.start, end=h.end,
                )
            case _:
                raise ValueError(f"malformed alignment item: {item!r}")


def _is_hum_row(row: AlignedWord) -> bool:
    heard = row["spoken_word"]
    return heard is not None and is_hum(heard)


def place_omissions(rows: list[AlignedWord]) -> list[AlignedWord]:
    """Gives each omitted word the time where it should have been read.

    The span is the gap between the last heard word before it and the first heard word after
    it; several omissions in a row share that gap by letter count. At the very start or end of
    the reading the span is zero-length at the edge. Run after MovedWordTagger, so the passage
    half of a moved word stays untimed.
    """
    out = list(rows)
    k = 0
    while k < len(out):
        if out[k]["tag"] != "omission":
            k += 1
            continue
        e = k
        while e < len(out) and out[e]["tag"] == "omission":
            e += 1
        before = next((r["end"] for r in reversed(out[:k]) if r["end"] is not None), None)
        after = next((r["start"] for r in out[e:] if r["start"] is not None), None)
        lo = before if before is not None else after
        hi = after if after is not None else before
        if lo is not None and hi is not None:
            hi = max(hi, lo)
            letters = [len(r["passage_word"] or "") or 1 for r in out[k:e]]
            t = lo
            for n, size in zip(range(k, e), letters):
                row = out[n].copy()
                row["start"], t = t, t + (hi - lo) * size / sum(letters)
                row["end"] = hi if n == e - 1 else t
                out[n] = row
        k = e
    return out
