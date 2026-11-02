from audio.alignment.moves import MoveRule
from audio.models import AlignedWord


class MovedWordTagger:
    """Last Lane A step: omitted words read elsewhere -> 'moved' halves sharing a move_id.

    'with his father Mang Ador' read 'with see Mang Ador father':
    father (passage spot, no audio) + father (heard spot, timed), same move_id, counted once.
    A run of omissions matching a run of insertions is one moved phrase; MoveRule says how far
    it may travel and how close its words must be. A misread word inside the phrase
    ('the old men') is its own miscue, like a misread swap half: its heard half is tagged
    substitution (post-pass -> mispronunciation), keeps the move_id and is counted.
    """

    def __init__(self, rule: MoveRule) -> None:
        """Injects the shared distance / closeness rule (same one MoveAwareAligner uses)."""
        self._rule = rule

    def tag(self, rows: list[AlignedWord]) -> list[AlignedWord]:
        """Same rows, same order; matched omission/insertion pairs retagged."""
        out = list(rows)
        next_id = max((r["move_id"] for r in rows if r["move_id"] is not None), default=0) + 1
        used: set[int] = set()
        for i, row in enumerate(rows):
            if i in used or row["tag"] != "omission" or row["passage_word"] is None:
                continue
            found = self._partner(rows, i, used)
            if found is None:
                continue
            j, n = found
            exact = [rows[i + k]["passage_word"] == rows[j + k]["spoken_word"] for k in range(n)]
            carrier = exact.index(True)  # the move is counted on its first exact word
            for k in range(n):
                out[i + k] = self._half(rows[i + k], next_id, counted=k == carrier)
                out[j + k] = (
                    self._half(rows[j + k], next_id, counted=False) if exact[k]
                    else self._misread(rows[j + k], rows[i + k], next_id)
                )
                used.update((i + k, j + k))
            next_id += 1
        return out

    def _partner(self, rows: list[AlignedWord], i: int, used: set[int]) -> tuple[int, int] | None:
        """(start of the insertion run, words in the move). Window is measured between the runs,
        so a long moved phrase doesn't push its partner out.
        """
        run = 1
        while i + run < len(rows) and rows[i + run]["tag"] == "omission":
            run += 1
        far = self._rule.far
        for j in range(max(0, i - far - run), min(len(rows), i + run + far + 1)):
            if j in used or rows[j]["tag"] != "insertion":
                continue
            gap = j - (i + run - 1) if j > i else i - (j + run - 1)
            pairs = self._run(rows, i, j, used, gap)
            if pairs and self._rule.anchored(pairs, gap):
                return j, len(pairs)
        return None

    def _run(self, rows: list[AlignedWord], i: int, j: int, used: set[int], gap: int) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        k = 0
        while i + k < len(rows) and j + k < len(rows) and i + k not in used and j + k not in used:
            expected, heard = rows[i + k]["passage_word"], rows[j + k]["spoken_word"]
            if (
                rows[i + k]["tag"] != "omission" or rows[j + k]["tag"] != "insertion"
                or expected is None or heard is None or not self._rule.close(expected, heard, gap)
            ):
                break
            pairs.append((expected, heard))
            k += 1
        return pairs

    @staticmethod
    def _half(row: AlignedWord, move_id: int, counted: bool) -> AlignedWord:
        out = row.copy()
        out["tag"] = "moved"
        out["move_id"] = move_id
        out["counted"] = counted
        return out

    @staticmethod
    def _misread(heard: AlignedWord, passage: AlignedWord, move_id: int) -> AlignedWord:
        """Heard half of a misread moved word: names the passage word it stood for, owns no passage index."""
        out = heard.copy()
        out["tag"] = "substitution"
        out["passage_word"] = passage["passage_word"]
        out["move_id"] = move_id
        out["counted"] = True
        return out
