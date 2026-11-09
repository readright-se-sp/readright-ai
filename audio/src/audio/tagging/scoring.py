from collections import Counter

from audio.models import COUNTED_TAGS, AlignedWord, MiscueCounts, ReadingLevel, ReadingScore


class ReadingScorer:
    """Counts rows -> word recognition %, WPM, Phil-IRI reading level."""

    def __init__(self, independent_min: float = 97.0, instructional_min: float = 91.0) -> None:
        """Reading-level cut-offs in word recognition %."""
        self._independent_min = independent_min
        self._instructional_min = instructional_min

    def score(self, rows: list[AlignedWord], total_words: int) -> ReadingScore:
        """One counted row = one error. A swap/move spans several rows but only its first is counted."""
        tally = Counter(r["tag"] for r in rows if r["counted"] or r["tag"] not in COUNTED_TAGS)
        counts = MiscueCounts(
            match=tally["match"],
            mispronunciation=tally["mispronunciation"],
            substitution=tally["substitution"],
            omission=tally["omission"],
            insertion=tally["insertion"],
            repetition=tally["repetition"],
            swap=tally["swap"],
            moved=tally["moved"],
            reversal=tally["reversal"],
            letter_transposition=tally["letter_transposition"],
            self_correction=tally["self_correction"],
            filler=tally["filler"],
            false_start=tally["false_start"],
            off_text=tally["off_text"],
        )
        errors = sum(1 for r in rows if r["counted"])
        pct = max(0.0, (total_words - errors) / total_words * 100) if total_words else 0.0
        return ReadingScore(
            counts=counts,
            total_words=total_words,
            error_count=errors,
            word_recognition_pct=round(pct, 1),
            wpm=self._wpm(rows, total_words),
            reading_level=self._level(pct),
        )

    def _level(self, pct: float) -> ReadingLevel:
        if pct >= self._independent_min:
            return "Independent"
        if pct >= self._instructional_min:
            return "Instructional"
        return "Frustration"

    @staticmethod
    def _wpm(rows: list[AlignedWord], total_words: int) -> float | None:
        """Over the reading only: talk before or after it (off_text) would make the child look slower."""
        reading = [r for r in rows if r["tag"] != "off_text"]
        starts = [r["start"] for r in reading if r["start"] is not None]
        ends = [r["end"] for r in reading if r["end"] is not None]
        if not starts or not ends or max(ends) <= min(starts):
            return None
        return round(total_words / (max(ends) - min(starts)) * 60, 1)
