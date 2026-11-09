from audio.models import AlignedWord, Tag, retag

_READ: frozenset[Tag] = frozenset({"match", "mispronunciation", "substitution"})


class AttemptJudge:
    """Post-pass: judges words read more than once. The last reading counts.

    Lane A finds the tries (NW re-read / retry state, repeats.py, swaps.py) and leaves them in
    `attempts`; judging happens here, after Lane B may have corrected the base tags.
      last reading right                     -> self_correction (not counted)   'pork park'
      last reading wrong, right word earlier -> keeps its tag + 'miscorrection' 'signals singles'
    A swap half keeps its swap tag; a correction there only shows in attempts.
    """

    def tag(self, rows: list[AlignedWord]) -> list[AlignedWord]:
        """Same rows; ones with attempts get their verdict."""
        return [self._judge(row) for row in rows]

    @staticmethod
    def _judge(row: AlignedWord) -> AlignedWord:
        target = row["passage_word"]
        if not row["attempts"] or row["tag"] not in _READ or target is None or len(row["passage_indices"]) != 1:
            return row
        if row["tag"] == "match":
            return retag(row, "self_correction")
        if target in row["attempts"]:
            return retag(row, row["tag"], "miscorrection")
        return row
