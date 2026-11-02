from collections.abc import Sequence
from typing import Protocol

from audio.alignment.cleanup import TranscriptCleaner
from audio.alignment.detection import Detector
from audio.alignment.needleman_wunsch import AlignerProtocol
from audio.alignment.off_text import OffTextDetector
from audio.alignment.repeats import RepeatFolder
from audio.alignment.swaps import SwapDetector
from audio.alignment.text import normalize, tokenize_passage
from audio.models import CONTRACT_VERSION, AlignedWord, AudioResult, SpokenWord
from audio.tagging.moved import MovedWordTagger
from audio.tagging.post_pass import PostPass
from audio.tagging.scoring import ReadingScorer
from audio.transcription.crisper_whisper import TranscriberProtocol


class RowReviewerProtocol(Protocol):
    """Lane B hook (phonemes + arbiter): re-judges Lane A rows. Must keep row order + coverage."""

    def review(self, wav_path: str, rows: list[AlignedWord]) -> list[AlignedWord]: ...


class WordLane:
    """Lane A after ASR: normalise -> NW (with re-reads) -> fold repeats -> swaps -> talk outside the reading
    -> detect -> moved (base tags).
    """

    def __init__(
        self,
        cleaner: TranscriptCleaner,
        aligner: AlignerProtocol,
        repeats: RepeatFolder,
        swaps: SwapDetector,
        off_text: OffTextDetector,
        detector: Detector,
        moved: MovedWordTagger,
    ) -> None:
        """All stages injected — wired in audio.wiring."""
        self._cleaner = cleaner
        self._aligner = aligner
        self._repeats = repeats
        self._swaps = swaps
        self._off_text = off_text
        self._detector = detector
        self._moved = moved

    def rows(self, spoken: list[SpokenWord], passage: list[str], proper_nouns: frozenset[str]) -> list[AlignedWord]:
        """[(passage_word, spoken_word, start, end, tag, move_id)] — the Lane A output."""
        cleaned = self._cleaner.clean(spoken, passage)
        items = self._repeats.fold(self._aligner.align(passage, cleaned.tokens), passage)
        items = self._off_text.mark(self._swaps.detect(items, passage))
        rows = self._detector.detect(items, cleaned.side_events, passage, proper_nouns)
        return self._moved.tag(rows)


class AudioPipeline:
    """ASR -> Lane A rows -> Lane B reviewers (optional) -> post-pass -> score.

    Blocking (model inference) — call through asyncio.to_thread from the API layer.
    """

    def __init__(
        self,
        transcriber: TranscriberProtocol,
        lane_a: WordLane,
        post_pass: PostPass,
        scorer: ReadingScorer,
        reviewers: Sequence[RowReviewerProtocol] = (),
    ) -> None:
        """reviewers = Lane B stages; empty until the phonetic lane exists."""
        self._transcriber = transcriber
        self._lane_a = lane_a
        self._post_pass = post_pass
        self._scorer = scorer
        self._reviewers = reviewers

    def run(self, wav_path: str, passage_text: str, proper_nouns: Sequence[str] = ()) -> AudioResult:
        """Full analysis of a 16 kHz mono WAV. Raises ValueError if the passage has no words."""
        return self._analyze(self._transcriber.transcribe(wav_path), passage_text, proper_nouns, wav_path)

    def analyze(self, spoken: list[SpokenWord], passage_text: str, proper_nouns: Sequence[str] = ()) -> AudioResult:
        """Everything after ASR, no audio -> Lane B skipped. For tests + replaying saved transcripts."""
        return self._analyze(spoken, passage_text, proper_nouns, None)

    def _analyze(
        self, spoken: list[SpokenWord], passage_text: str, proper_nouns: Sequence[str], wav_path: str | None
    ) -> AudioResult:
        passage = tokenize_passage(passage_text)
        if not passage:
            raise ValueError("passage has no words")
        nouns = frozenset(w for name in proper_nouns for w in normalize(name))
        rows = self._lane_a.rows(spoken, passage, nouns)
        if wav_path is not None:
            for reviewer in self._reviewers:
                rows = reviewer.review(wav_path, rows)
        rows = self._post_pass.run(rows, nouns)
        return AudioResult(version=CONTRACT_VERSION, score=self._scorer.score(rows, len(passage)), words=rows, transcript=spoken)
