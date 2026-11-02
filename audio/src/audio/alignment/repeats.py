from audio.models import AlignedItem, Column, Repeat, Swap, Token


class RepeatFolder:
    """NW rep columns -> one Repeat per re-read. Last reading of each word wins.

    'they can they can produce' -> match match rep rep match -> Repeat(heard: first 'they can',
    columns: second 'they can'). Back-to-back jumps ('they can' x3) are one Repeat, so one error.
    A re-read whose last two words come crossed ('happy was, happy wos') still ends in a swap:
    Repeat for the rest + a Swap carrying the pair's earlier readings as tries.
    """

    def fold(self, columns: list[Column], passage: list[str]) -> list[AlignedItem]:
        """Same columns with each rep run and the stretch it re-read replaced by a Repeat."""
        out: list[AlignedItem] = []
        i = 0
        while i < len(columns):
            if columns[i].op != "rep":
                out.append(columns[i])
                i += 1
                continue
            end = i
            while end < len(columns) and columns[end].op == "rep":
                end += 1
            reps = columns[i:end]
            prior = self._take_stretch(out, min(c.p for c in reps if c.p is not None))
            repeat = self._repeat(prior, reps, passage)
            pair = self._crossed_pair(reps)
            if pair is None:
                out.append(repeat)
            else:
                out.extend(self._split_swap(repeat, *pair))
            i = end
        return out

    @staticmethod
    def _crossed_pair(reps: list[Column]) -> tuple[int, int] | None:
        """(p, q) when the run ends 'rep q, rep p' with q = p + 1 (NW's crossed re-read)."""
        if len(reps) < 2:
            return None
        a, b = reps[-2].p, reps[-1].p
        return (b, a) if a is not None and b is not None and a == b + 1 else None

    @staticmethod
    def _split_swap(repeat: Repeat, p: int, q: int) -> list[AlignedItem]:
        """Pair p, q is the last thing re-read, so the Swap goes after the rest of the Repeat."""
        keep = [k for k, c in enumerate(repeat.columns) if c.p not in (p, q)]
        final = {c.p: (c.h, t) for c, t in zip(repeat.columns, repeat.tries) if c.p in (p, q)}
        (hp, tp), (hq, tq) = final[p], final[q]
        if hp is None or hq is None:
            return [repeat]
        rest = Repeat(repeat.heard, tuple(repeat.columns[k] for k in keep), tuple(repeat.tries[k] for k in keep))
        swap = Swap(p, q, (hq, hp), tries=((p, tp), (q, tq)))
        return [rest, swap] if rest.heard or rest.columns else [swap]

    @staticmethod
    def _take_stretch(out: list[AlignedItem], first: int) -> Repeat:
        """Pops what the jump went back over (passage word `first` up to the rep run) as one Repeat.

        An earlier Repeat inside that span is absorbed: 'ben, ben runs, ben runs' is one repetition.
        """
        k = len(out)
        while k > 0:
            item = out[k - 1]
            if isinstance(item, Column) and (item.p is None or item.p >= first):
                k -= 1
            elif isinstance(item, Repeat) and any(c.p is not None and c.p >= first for c in item.columns):
                k -= 1
            else:
                break
        while k < len(out) and isinstance(col := out[k], Column) and col.p is None:
            k += 1  # insertions before the stretch aren't part of it
        heard: list[Token] = []
        columns: list[Column] = []
        tries: list[tuple[str, ...]] = []
        for item in out[k:]:
            if isinstance(item, Repeat):
                heard.extend(item.heard)
                columns.extend(item.columns)
                tries.extend(item.tries)
            elif isinstance(item, Column):
                columns.append(item)
                tries.append(())
        del out[k:]
        return Repeat(tuple(heard), tuple(columns), tuple(tries))

    @staticmethod
    def _repeat(prior: Repeat, reps: list[Column], passage: list[str]) -> Repeat:
        stretch = prior.columns
        old_tries = {c.p: t for c, t in zip(stretch, prior.tries) if c.p is not None}
        readings: dict[int, list[Token]] = {c.p: [] for c in stretch if c.p is not None}
        heard: list[Token] = list(prior.heard)
        extra: list[Token] = []  # words added between the copies ('I… and I will')
        for c in [*stretch, *reps]:
            if c.h is None:
                continue
            if c.p is None:
                extra.append(c.h)
            elif c.p not in readings:
                heard.append(c.h)  # re-read of a word outside the stretch
            else:
                readings[c.p].append(c.h)
        same: list[Token] = []
        words: list[Column] = []
        tries: list[tuple[str, ...]] = []
        for c in stretch:
            if c.p is None:
                continue
            got = readings[c.p]
            if not got:
                words.append(Column("del", c.p, None))
                tries.append(old_tries[c.p])
                continue
            earlier, last = got[:-1], got[-1]
            same.extend(t for t in earlier if t.norm == last.norm)
            tries.append((*old_tries[c.p], *(t.norm for t in earlier if t.norm != last.norm)))
            words.append(Column("match" if last.norm == passage[c.p] else "sub", c.p, last))
        # A repetition is said again straight away. With words added in between, the earlier
        # copy isn't repeated, it's extra words: insertions ('I and' in 'I… and I will').
        if extra:
            extra += same
        else:
            heard += same
        inserted = [Column("ins", None, t) for t in sorted(extra, key=lambda t: t.id)]
        return Repeat(
            tuple(sorted(heard, key=lambda t: t.id)), (*inserted, *words), ((),) * len(inserted) + tuple(tries)
        )
