from typing import Protocol

from audio.alignment.text import cut_like, is_anagram, is_hum, is_proper_noun, sound_key, word_distance
from audio.models import Column, Token

_INF = 1 << 60
_M, _D, _I = 0, 1, 2  # last move: diagonal / deletion (passage word unread) / insertion
_X = 3  # last move: transposition — outer words of a span exchanged, words between read in place
_S, _G = 4, 5  # last move: one heard word read 2-3 passage words (split) / 2-3 heard pieces read one (merge)
_R = 6  # state _R + k: re-reading, k words left to catch up (k = 0: just caught up)
_FUZZY_SPAN = 3  # spans up to this may cross near-matches ('happy wos'); wider ones need exact words
_ANCHOR_SPAN, _ANCHOR_LEN = 8, 4  # wider spans need a 4+ letter crossed word ('the'/'a' swap by chance)
_ANAGRAM_COST = 0.45  # same letters reordered (saw/was) pairs as a close misreading, not a stranger
_NAME_SIMILAR_BELOW = 0.7  # a name pairs with a heard word this far off ('montenoys' ~ 'tinoys')
_SCALE = 1000  # costs in integer milli-units so equal-cost ties are exact


class AlignerProtocol(Protocol):
    """Interface for passage<->heard aligners — pipeline depends on this, not NW."""

    def align(self, passage: list[str], heard: list[Token], names: frozenset[str] = frozenset()) -> list[Column]: ...


class NeedlemanWunschAligner:
    """Global alignment with affine gaps (Gotoh) + re-read and transposition states.
    Graded mismatch cost, ties prefer diagonal.

    Affine gaps keep a skipped phrase as ONE run of omissions instead of splitting it.
    Re-read (repetition): the reader jumps back up to max_repeat words and reads on until they
    catch up with where they were. repeat_open is paid once per jump, each re-read word costs like
    a diagonal pairing — so a repeat of any length is one cheap event and near-copies still fit.
    A jump must catch up: re-reading an unread word (swap, move) can't end there. The last two
    words re-read may be crossed: 'happy was, happy was' is a swap read twice, not two insertions.
    Retries are re-reads too: jumping back one word straight after reading it is cheaper
    (retry_open: 'pork park', 'signals singles') and jumping again straight after catching up is
    nearly free (rejump: 'pork perk park' is one retry). This is where self-corrections are found;
    the post-pass only judges them (tagging/attempts.py).
    A re-read says the same words again, so an unrelated word inside one costs extra
    (reread_stray): 'yelled Mom. And yelled Jackie. And yet Jackie' is 'yet' misread then
    re-read, not a jump back to 'yelled' with 'Jackie' standing in for 'Mom'.
    Transposition (Damerau-style): two words read in each other's places, with up to
    max_swap_span - 2 words between them read in place ('happy was', 'ripe fish fresh',
    'the cot sat on the cat'). swap_cost once + the pairings. Crossed words must be similar
    within _FUZZY_SPAN ('happy wos' fits, 'happy dog' doesn't) and exact beyond it; at least
    one of the two is always exact ('hapy wos' is two misreadings, not a swap — OPEN_QUESTIONS.md).
    Past _ANCHOR_SPAN one crossed word needs _ANCHOR_LEN+ letters, as for moves (moves.MoveRule).
    Split / merge, for words the ASR glued or cut: one heard word reads 2-3 neighbouring passage
    words ("Mang Tinoy's" heard 'Montenoy's'), or 2-3 heard pieces read one ("Juaning's" heard
    'Jo ang'). join_cost + how close the joined form is. Only where it sits in the grid, only if
    the joined form is close and closer than any single word, so 'village' for 'visit' isn't cut
    to match 'will have' elsewhere, and 'tinoys' alone doesn't swallow a skipped 'mang'.
    """

    def __init__(
        self,
        gap_open: float = 0.9,
        gap_extend: float = 0.6,
        similar_below: float = 0.5,
        repeat_open: float = 0.5,
        retry_open: float = 0.3,
        rejump: float = 0.25,
        reread_stray: float = 0.5,
        max_repeat: int = 20,
        swap_cost: float = 0.5,
        max_swap_span: int = 22,
        join_cost: float = 0.2,
    ) -> None:
        """gap_extend must stay > 0.5: else long del+ins runs beat honest 1-to-1 mismatches.

        Word pairs closer than similar_below cost their distance; anything else costs a flat 1.0,
        so NW can't build a cheap chain of unrelated pairings around a moved word.
        0 < rejump <= retry_open <= repeat_open < gap_open: any re-read beats an insertion, never
        ties forward reading. retry_open = jump back one word; rejump = jump again right after
        catching up (still the same event, as repeats.py folds it). retry_open + rejump >
        repeat_open, so a long repeat is never split into a cheap retry + rejump.
        reread_stray > 0 = extra cost of a re-read word unlike the passage word it re-reads.
        max_repeat = furthest jump back, in words (only bounds run time).
        0 < swap_cost < gap_open: a swap beats its del+ins pair, never ties reading in order.
        max_swap_span = passage words a transposition covers, both ends included (2 = neighbours;
        22 = up to 20 words between, the same reach as a move).
        0 < join_cost < gap_open: charge for a split or merge, so 1-to-1 pairing wins a tie.
        """
        if gap_extend <= 0.5 or gap_open < gap_extend:
            raise ValueError("need gap_open >= gap_extend > 0.5")
        if not 0 < rejump <= retry_open <= repeat_open < gap_open or retry_open + rejump <= repeat_open:
            raise ValueError("need 0 < rejump <= retry_open <= repeat_open < gap_open, retry_open + rejump > repeat_open")
        if not 0 < swap_cost < gap_open:
            raise ValueError("need 0 < swap_cost < gap_open")
        if reread_stray <= 0:
            raise ValueError("need reread_stray > 0")
        if not 0 < join_cost < gap_open:
            raise ValueError("need 0 < join_cost < gap_open")
        if max_repeat < 1 or max_swap_span < 2:
            raise ValueError("need max_repeat >= 1 and max_swap_span >= 2")
        self._open = round(gap_open * _SCALE)
        self._extend = round(gap_extend * _SCALE)
        self._repeat_open = round(repeat_open * _SCALE)
        self._retry_open = round(retry_open * _SCALE)
        self._rejump = round(rejump * _SCALE)
        self._reread_stray = round(reread_stray * _SCALE)
        self._swap = round(swap_cost * _SCALE)
        self._join = round(join_cost * _SCALE)
        self._max_repeat = max_repeat
        self._max_swap_span = max_swap_span
        self._similar_below = similar_below

    def align(self, passage: list[str], heard: list[Token], names: frozenset[str] = frozenset()) -> list[Column]:
        """Returns columns in reading order. Every passage word appears once outside rep columns,
        every heard token exactly once. `names` = proper nouns: a heard word pairs with a name more
        readily (_name_cost), so a misread name is one misreading, not an omission + insertion.
        """
        n, m = len(passage), len(heard)
        cost = [
            [self._name_cost(p, t.norm) if is_proper_noun(p, names) else self._cost(p, t.norm) for t in heard]
            for p in passage
        ]
        diag = [[0] * (m + 1) for _ in range(n + 1)]  # diag[i][j] = cost of pairing the i, j before, in step
        states = _R + self._max_repeat + 1
        score = [[[_INF] * (m + 1) for _ in range(n + 1)] for _ in range(states)]
        back = [[[_M] * (m + 1) for _ in range(n + 1)] for _ in range(states)]
        span = [[0] * (m + 1) for _ in range(n + 1)]  # width of the transposition ending here
        crossed = [[False] * (m + 1) for _ in range(n + 1)]  # re-read ending here took its last two crossed
        split_w = [[0] * (m + 1) for _ in range(n + 1)]  # passage words the split ending here covers
        merge_w = [[0] * (m + 1) for _ in range(n + 1)]  # heard pieces the merge ending here joins
        score[_M][0][0] = 0
        for i in range(1, n + 1):
            score[_D][i][0] = self._open + (i - 1) * self._extend
            back[_D][i][0] = _M if i == 1 else _D
        for j in range(1, m + 1):
            score[_I][0][j] = self._open + (j - 1) * self._extend
            back[_I][0][j] = _M if j == 1 else _I

        for i in range(1, n + 1):
            for j in range(1, m + 1):
                c = cost[i - 1][j - 1]
                diag[i][j] = diag[i - 1][j - 1] + c
                self._fill(score[_M], back[_M], i, j, [s[i - 1][j - 1] + c for s in score[: _R + 1]])
                self._fill(score[_D], back[_D], i, j, self._gap_from(score, i - 1, j, _D))
                self._fill(score[_I], back[_I], i, j, self._gap_from(score, i, j - 1, _I))
                self._fill_swap(score, back, span, cost, diag, passage, i, j)
                self._fill_split(score, back, split_w, cost, passage, heard, i, j)
                self._fill_merge(score, back, merge_w, cost, passage, heard, i, j)
                self._fill_reread(score, back, cost, i, j)
                self._fill_crossed_reread(score, back, crossed, cost, passage, i, j)

        return self._trace(score, back, span, crossed, split_w, merge_w, passage, heard)

    def _cost(self, expected: str, heard: str) -> int:
        if is_hum(expected) and is_hum(heard):
            return 0  # 'um' reads 'Mmm'
        d = word_distance(expected, heard)
        if is_anagram(expected, heard):
            d = min(d, _ANAGRAM_COST)
        return round((d if d < self._similar_below else 1.0) * _SCALE)

    def _name_cost(self, name: str, heard: str) -> int:
        """Pairing cost at a name: a same-sounding spelling is free ('jackie' for 'jacky'), and a
        looser 'close' (_NAME_SIMILAR_BELOW): unfamiliar names get sounded out far from the spelling.
        """
        if sound_key(name) == sound_key(heard):
            return 0
        d = word_distance(name, heard)
        return round((d if d < _NAME_SIMILAR_BELOW else 1.0) * _SCALE)

    def _gap_from(self, score: list[list[list[int]]], i: int, j: int, state: int) -> list[int]:
        return [s[i][j] + (self._extend if k == state else self._open) for k, s in enumerate(score[: _R + 1])]

    def _fill_swap(
        self,
        score: list[list[list[int]]],
        back: list[list[list[int]]],
        span: list[list[int]],
        cost: list[list[int]],
        diag: list[list[int]],
        passage: list[str],
        i: int,
        j: int,
    ) -> None:
        """Last w heard words read the last w passage words with the outer pair crossed, middle in step."""
        for w in range(2, min(self._max_swap_span, i, j) + 1):
            first, last = cost[i - w][j - 1], cost[i - 1][j - w]  # crossed: passage i-w heard last, and back
            limit = _SCALE if w <= _FUZZY_SPAN else 1  # near-match close by, exact further apart
            if first >= limit or last >= limit or (first and last):
                continue  # an unrelated word isn't half of a swap; two misread halves are two misreadings
            if w > _ANCHOR_SPAN and max(len(passage[i - w]), len(passage[i - 1])) < _ANCHOR_LEN:
                continue
            middle = diag[i - 1][j - 1] - diag[i - w + 1][j - w + 1]
            src = min(range(_R + 1), key=lambda s: score[s][i - w][j - w])
            total = score[src][i - w][j - w] + self._swap + first + last + middle
            if total < score[_X][i][j]:  # narrowest span wins ties
                score[_X][i][j], back[_X][i][j], span[i][j] = total, src, w

    def _fill_split(
        self,
        score: list[list[list[int]]],
        back: list[list[list[int]]],
        split_w: list[list[int]],
        cost: list[list[int]],
        passage: list[str],
        heard: list[Token],
        i: int,
        j: int,
    ) -> None:
        """heard[j-1] reads the last 2 or 3 passage words, glued ('montenoys' for mang tinoys)."""
        word = heard[j - 1].norm
        for w in (2, 3):
            if i < w:
                break
            parts = passage[i - w : i]
            d = self._joined_cost("".join(parts), word, alone=min(cost[k][j - 1] for k in range(i - w, i)))
            if d is None or not all(cut_like(word, parts)):
                continue
            src = min(range(_R + 1), key=lambda s: score[s][i - w][j - 1])
            total = score[src][i - w][j - 1] + self._join + d
            if total < score[_S][i][j]:
                score[_S][i][j], back[_S][i][j], split_w[i][j] = total, src, w

    def _fill_merge(
        self,
        score: list[list[list[int]]],
        back: list[list[list[int]]],
        merge_w: list[list[int]],
        cost: list[list[int]],
        passage: list[str],
        heard: list[Token],
        i: int,
        j: int,
    ) -> None:
        """The last 2 or 3 heard pieces read passage word i-1, cut apart ('jo ang' for juanings).

        Also compared without a possessive 's' ('joang' ~ 'juaning'): children drop it.
        """
        target = passage[i - 1]
        for w in (2, 3):
            if j < w:
                break
            joined = "".join(t.norm for t in heard[j - w : j])
            alone = min(cost[i - 1][k] for k in range(j - w, j))
            forms = [target, target[:-1]] if target.endswith("s") and len(target) > 3 else [target]
            costs = [c for form in forms if (c := self._joined_cost(form, joined, alone)) is not None]
            if not costs:
                continue
            d = min(costs)
            src = min(range(_R + 1), key=lambda s: score[s][i - 1][j - w])
            total = score[src][i - 1][j - w] + self._join + d
            if total < score[_G][i][j]:
                score[_G][i][j], back[_G][i][j], merge_w[i][j] = total, src, w

    def _joined_cost(self, expected: str, heard: str, alone: int) -> int | None:
        """Cost of a joined form, or None unless it's close AND closer than any single word (`alone`).

        Cheap checks first: same first letter, similar length.
        """
        if not expected or not heard or expected[0] != heard[0]:
            return None
        if abs(len(expected) - len(heard)) >= max(len(expected), len(heard)) / 2:
            return None
        d = self._cost(expected, heard)
        return d if d < round(self._similar_below * _SCALE) and d < alone else None

    def _fill_reread(
        self, score: list[list[list[int]]], back: list[list[list[int]]], cost: list[list[int]], i: int, j: int
    ) -> None:
        """heard[j-1] re-reads passage word i-k-1, landing in _R + k: jump back k+1 words, or carry on.

        A jump costs repeat_open; retry_open if it's one word back straight after reading that word
        (state _M); rejump if it starts straight from a just-finished re-read (state _R).
        A re-read word unlike its passage word costs reread_stray on top.
        """
        src = min(range(_R), key=lambda s: score[s][i][j - 1])
        fresh = (score[src][i][j - 1] + self._repeat_open, src)
        retry = (score[_M][i][j - 1] + self._retry_open, _M)
        again = (score[_R][i][j - 1] + self._rejump, _R)
        for k in range(min(self._max_repeat, i)):
            c = cost[i - k - 1][j - 1]
            if c >= _SCALE:
                c += self._reread_stray
            jump, jump_src = min(fresh, again, retry) if k == 0 else min(fresh, again)
            carry = score[_R + k + 1][i][j - 1] + c
            if carry <= jump + c:  # tie -> one long re-read, not two jumps
                score[_R + k][i][j], back[_R + k][i][j] = carry, _R + k + 1
            else:
                score[_R + k][i][j], back[_R + k][i][j] = jump + c, jump_src

    def _fill_crossed_reread(
        self,
        score: list[list[list[int]]],
        back: list[list[list[int]]],
        crossed: list[list[bool]],
        cost: list[list[int]],
        passage: list[str],
        i: int,
        j: int,
    ) -> None:
        """Re-read whose last two words come crossed: heard j-2 reads passage i-1, heard j-1 reads i-2.

        'she happy was, happy was': the swap read twice. Same crossing rule as a swap, no swap_cost —
        the transposition is one event, counted once however often it's read.
        """
        if i < 2 or j < 2 or self._max_repeat < 2 or passage[i - 1] == passage[i - 2]:
            return
        a, b = cost[i - 1][j - 2], cost[i - 2][j - 1]
        if a >= _SCALE or b >= _SCALE or (a and b):
            return
        src = min(range(_R + 1), key=lambda s: score[s][i][j - 2])
        best, prev = min(
            (score[_R + 2][i][j - 2] + a + b, _R + 2),
            (score[src][i][j - 2] + self._repeat_open + a + b, src),
        )
        if best < score[_R][i][j]:
            score[_R][i][j], back[_R][i][j], crossed[i][j] = best, prev, True

    @staticmethod
    def _fill(score: list[list[int]], back: list[list[int]], i: int, j: int, options: list[int]) -> None:
        best = min(range(len(options)), key=lambda k: options[k])  # first minimum -> diagonal wins ties
        score[i][j] = options[best]
        back[i][j] = best

    @staticmethod
    def _trace(
        score: list[list[list[int]]],
        back: list[list[list[int]]],
        span: list[list[int]],
        crossed: list[list[bool]],
        split_w: list[list[int]],
        merge_w: list[list[int]],
        passage: list[str],
        heard: list[Token],
    ) -> list[Column]:
        i, j = len(passage), len(heard)
        state = min(range(_R + 1), key=lambda k: score[k][i][j])  # never end mid-repeat
        columns: list[Column] = []
        while i > 0 or j > 0:
            prev = back[state][i][j]
            if state == _M:
                tok = heard[j - 1]
                op = "match" if tok.norm == passage[i - 1] else "sub"
                columns.append(Column(op, i - 1, tok))
                i, j = i - 1, j - 1
            elif state == _D:
                columns.append(Column("del", i - 1, None))
                i -= 1
            elif state == _I:
                columns.append(Column("ins", None, heard[j - 1]))
                j -= 1
            elif state == _X:
                w = span[i][j]  # appended backwards: reversed below into spoken order
                columns.append(Column("swp", i - w, heard[j - 1]))
                columns.extend(Column("mid", i - k, heard[j - k]) for k in range(2, w))
                columns.append(Column("swp", i - 1, heard[j - w]))
                i, j = i - w, j - w
            elif state == _S:  # one heard word cut into one piece per passage word (appended backwards)
                w = split_w[i][j]
                parts = passage[i - w : i]
                pieces = _share_timing(heard[j - 1], cut_like(heard[j - 1].norm, parts))
                for k in reversed(range(w)):
                    op = "match" if pieces[k].norm == parts[k] else "sub"
                    columns.append(Column(op, i - w + k, pieces[k]))
                i, j = i - w, j - 1
            elif state == _G:  # heard pieces joined into one word, timed first start -> last end
                w = merge_w[i][j]
                group = heard[j - w : j]
                tok = Token(
                    group[0].id, "".join(t.norm for t in group), " ".join(t.raw for t in group),
                    group[0].start, group[-1].end,
                )
                columns.append(Column("match" if tok.norm == passage[i - 1] else "sub", i - 1, tok))
                i, j = i - 1, j - w
            elif state == _R and crossed[i][j]:  # spoken order after the reverse: rep i-1, rep i-2
                columns.append(Column("rep", i - 2, heard[j - 1]))
                columns.append(Column("rep", i - 1, heard[j - 2]))
                j -= 2
            else:
                columns.append(Column("rep", i - (state - _R) - 1, heard[j - 1]))
                j -= 1
            state = prev
        columns.reverse()
        return columns


def _share_timing(tok: Token, pieces: list[str]) -> list[Token]:
    """Pieces of one heard token, its time span shared by letter count. They keep the token's id."""
    if tok.start is None or tok.end is None:
        return [Token(tok.id, piece, tok.raw, None, None) for piece in pieces]
    total = sum(len(piece) for piece in pieces)
    out: list[Token] = []
    t = tok.start
    for piece in pieces:
        nxt = t + (tok.end - tok.start) * len(piece) / total
        out.append(Token(tok.id, piece, tok.raw, t, nxt))
        t = nxt
    return out
