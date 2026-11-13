from audio.alignment.labels import WordLabeler
from audio.models import AlignedWord, retag


class MispronunciationTagger:
    """Post-pass: substitution close in spelling to the passage word -> mispronunciation.

    'tomatoes' -> 'tomato' (ending), 'coast' -> 'cost' (close). Unrelated words stay substitution.
    """

    def __init__(self, labeler: WordLabeler) -> None:
        """Injects the letter-based labeler (same rules the attempt tagger uses)."""
        self._labeler = labeler

    def tag(self, rows: list[AlignedWord], proper_nouns: frozenset[str]) -> list[AlignedWord]:
        """Only single-word substitution rows change. An existing detail (miscorrection) is kept.

        Rows Lane B judged by sound (phonetic.check == "done") are left alone: its label stands.
        """
        out: list[AlignedWord] = []
        for row in rows:
            expected, heard = row["passage_word"], row["spoken_word"]
            judged = row.get("phonetic", {}).get("check") == "done"
            if judged or row["tag"] != "substitution" or expected is None or heard is None or " " in heard:
                out.append(row)
                continue
            label = self._labeler.label(expected, heard, proper_nouns)
            out.append(retag(row, label.tag, row["detail"] or label.detail) if label.tag != row["tag"] else row)
        return out
