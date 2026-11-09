from audio.alignment.text import is_letter_transposition, is_reversal
from audio.models import AlignedWord, retag


class ReversalTagger:
    """Post-pass: misread word with the passage word's letters out of order.

    Phil-IRI: transposition can happen inside a word; reversal is the specific case where the
    word is read backwards (flipped / mirrored).
      reversal             was/saw, pot/top, on/no
      letter_transposition from/form, animal/aminal, girls/gril (letters swapped, maybe + a slip)
    Sound reversals (his -> "see") need Lane B phonemes; until then they stay
    mispronunciation/substitution (still counted). Keeps the row's detail, so a miscorrection stays visible.
    """

    def tag(self, rows: list[AlignedWord]) -> list[AlignedWord]:
        """Rows with mispronunciation/substitution re-checked; everything else unchanged. One miscue per word."""
        out: list[AlignedWord] = []
        for row in rows:
            expected, heard = row["passage_word"], row["spoken_word"]
            if row["tag"] not in ("mispronunciation", "substitution") or expected is None or heard is None:
                out.append(row)
            elif is_reversal(expected, heard):
                out.append(retag(row, "reversal", row["detail"]))
            elif is_letter_transposition(expected, heard):
                out.append(retag(row, "letter_transposition", row["detail"]))
            else:
                out.append(row)
        return out
