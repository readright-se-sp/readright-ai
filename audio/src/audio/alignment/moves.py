from audio.alignment.needleman_wunsch import AlignerProtocol
from audio.alignment.text import word_distance
from audio.models import Column, Token


class MoveRule:
    """How far a word or phrase may travel and still be 'moved', and how closely it must match.

    The farther it went, the stricter: up to `near` words away a moved word may be a close
    misreading ('men' for 'man'); up to `far`, only a very close one. Every move needs an exact
    anchor word: a lone misread word that moved is 2 errors either way (moved + mispronunciation
    = omission + insertion), so allowing it would only add false moves. Far moves need a long
    anchor or two exact words, so 'the' / 'a' can't make one by coincidence.
    """

    def __init__(
        self, near: int = 6, far: int = 20, near_below: float = 0.5, far_below: float = 0.25, anchor_len: int = 4
    ) -> None:
        """near/far in words; *_below = word_distance limits for a misread moved word; anchor_len = letters."""
        self.far = far
        self._near = near
        self._near_below = near_below
        self._far_below = far_below
        self._anchor_len = anchor_len

    def close(self, expected: str, heard: str, gap: int) -> bool:
        """Heard word can stand for the passage word in a move that travelled `gap` words."""
        if expected == heard:
            return True
        if len(expected) < 3 or gap > self.far:
            return False
        return word_distance(expected, heard) < (self._near_below if gap <= self._near else self._far_below)

    def anchored(self, pairs: list[tuple[str, str]], gap: int) -> bool:
        """(passage word, heard word) pairs of one move: enough exact words to trust it."""
        exact = [e for e, h in pairs if e == h]
        if not exact or gap > self.far:
            return False
        return gap <= self._near or len(exact) >= 2 or any(len(e) >= self._anchor_len for e in exact)


class MoveAwareAligner:
    """Wraps NW and undoes the slide a moved word causes.

    "with his father Mang Ador" read "with see Mang Ador father": plain NW pairs
    his->see, father->mang... One heard word that exactly matches an UNREAD passage word
    up to rule.far columns away is set aside, the rest re-aligned, and the pair put back as
    del (passage spot) + ins (heard spot). tagging/moved joins them (and any misread
    neighbours of a moved phrase). Two words read in each other's places are NW's
    transposition state, not a move.
    A move is kept only if it doesn't add errors: 'the cot sat on the cut' for 'the cat sat
    on the cot' is 2 misreadings, and moving 'cot' would make it moved + omission + insertion = 3.
    """

    def __init__(self, inner: AlignerProtocol, rule: MoveRule, max_moves: int = 3, max_tries: int = 6) -> None:
        """Moves need >= 2 columns of distance; a word one column off is NW wobble, not a move.

        max_moves = moves kept; max_tries = candidate moves checked (each costs one NW run).
        """
        self._inner = inner
        self._rule = rule
        self._max_moves = max_moves
        self._max_tries = max_tries

    def align(self, passage: list[str], heard: list[Token]) -> list[Column]:
        """Same contract as NW: every passage word + heard token in exactly one column."""
        moved_p: set[int] = set()
        moved_h: set[int] = set()
        columns = self._align_without(passage, heard, moved_p, moved_h)
        errors = _errors(columns, passage)
        tried: set[tuple[int, int]] = set()
        for _ in range(self._max_tries):
            if len(moved_p) >= self._max_moves:
                break
            move = self._find_move(columns, passage, tried)
            if move is None:
                break
            tried.add(move)
            candidate = self._align_without(passage, heard, moved_p | {move[0]}, moved_h | {move[1]})
            candidate_errors = _errors(candidate, passage) + 1  # the move itself is one error
            if candidate_errors <= errors:  # tie -> keep: an exact word explained beats two stray pairings
                moved_p.add(move[0])
                moved_h.add(move[1])
                columns, errors = candidate, candidate_errors
        return self._put_back(columns, heard, moved_p, moved_h)

    def _align_without(
        self, passage: list[str], heard: list[Token], moved_p: set[int], moved_h: set[int]
    ) -> list[Column]:
        keep = [i for i in range(len(passage)) if i not in moved_p]
        columns = self._inner.align([passage[i] for i in keep], [t for t in heard if t.id not in moved_h])
        return [Column(c.op, None if c.p is None else keep[c.p], c.h) for c in columns]

    def _find_move(
        self, columns: list[Column], passage: list[str], tried: set[tuple[int, int]]
    ) -> tuple[int, int] | None:
        far = self._rule.far
        for i, col in enumerate(columns):
            if col.op not in ("sub", "ins") or col.h is None:
                continue
            word = col.h.norm
            for j in range(max(0, i - far), min(len(columns), i + far + 1)):
                other = columns[j]
                if (
                    abs(j - i) >= 2 and other.op in ("sub", "del") and other.p is not None
                    and passage[other.p] == word and (other.p, col.h.id) not in tried
                    and self._rule.anchored([(word, word)], abs(j - i))
                ):
                    return other.p, col.h.id
        return None

    @staticmethod
    def _put_back(columns: list[Column], heard: list[Token], moved_p: set[int], moved_h: set[int]) -> list[Column]:
        out = list(columns)
        for p in sorted(moved_p):
            at = next((k for k, c in enumerate(out) if c.p is not None and c.p > p), len(out))
            out.insert(at, Column("del", p, None))
        for tok in sorted((t for t in heard if t.id in moved_h), key=lambda t: t.id):
            at = max((k + 1 for k, c in enumerate(out) if c.h is not None and c.h.id < tok.id), default=0)
            out.insert(at, Column("ins", None, tok))
        return out


def _errors(columns: list[Column], passage: list[str]) -> int:
    """Errors an alignment will score, judging each word by its LAST reading (as the post-pass does).

    Each run of insertions, wrong or missing final reading, swap, and re-read run that repeats a
    word as it was said before (a retry that only fixes a word is a self-correction, not counted).
    """
    final: dict[int, str | None] = {}
    n = 0
    swaps = 0
    in_rep = False
    repeated = False
    prev_ins = False
    for c in columns:
        if c.op == "rep":
            if c.p is None or c.h is None:
                continue
            if not in_rep:
                in_rep, repeated = True, False
            if final.get(c.p) == c.h.norm and not repeated:
                n, repeated = n + 1, True
            final[c.p] = c.h.norm
            prev_ins = False
            continue
        in_rep = False
        if c.op == "ins":
            n += int(not prev_ins)  # a run of insertions is one error
        elif c.p is not None:
            final[c.p] = None if c.h is None else c.h.norm
        prev_ins = c.op == "ins"
        swaps += c.op == "swp"
    n += sum(1 for p, heard in final.items() if heard != passage[p])
    return n + swaps // 2
