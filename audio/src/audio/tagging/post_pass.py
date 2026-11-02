from audio.models import AlignedWord, Tag
from audio.tagging.attempts import AttemptJudge
from audio.tagging.mispronunciation import MispronunciationTagger
from audio.tagging.reversal import ReversalTagger

_NOISE: frozenset[Tag] = frozenset({"filler", "false_start"})


class PostPass:
    """Derived tags after the arbiter: attempts (self-correction) -> mispronunciation -> reversal,
    then insertion runs counted once.
    """

    def __init__(
        self, attempts: AttemptJudge, mispronunciation: MispronunciationTagger, reversal: ReversalTagger
    ) -> None:
        """Injects the three taggers. Order matters: tries are judged before the final word is relabelled."""
        self._attempts = attempts
        self._mispronunciation = mispronunciation
        self._reversal = reversal

    def run(self, rows: list[AlignedWord], proper_nouns: frozenset[str]) -> list[AlignedWord]:
        """Final rows. Every passage word still covered exactly once."""
        rows = self._attempts.tag(rows)
        rows = self._mispronunciation.tag(rows, proper_nouns)
        return _count_insertion_runs(self._reversal.tag(rows))


def _count_insertion_runs(rows: list[AlignedWord]) -> list[AlignedWord]:
    """Contiguous insertions are one error ('I think this good'): only the run's first row counts.

    Fillers and false starts between them don't break the run; any other row does.
    """
    out: list[AlignedWord] = []
    in_run = False
    for row in rows:
        if row["tag"] == "insertion":
            if in_run:
                row = row.copy()
                row["counted"] = False
            in_run = True
        elif row["tag"] not in _NOISE:
            in_run = False
        out.append(row)
    return out
