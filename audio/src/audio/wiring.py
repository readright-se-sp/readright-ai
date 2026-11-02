from audio.alignment.cleanup import TranscriptCleaner
from audio.alignment.detection import Detector
from audio.alignment.labels import WordLabeler
from audio.alignment.moves import MoveAwareAligner, MoveRule
from audio.alignment.needleman_wunsch import NeedlemanWunschAligner
from audio.alignment.off_text import OffTextDetector
from audio.alignment.repeats import RepeatFolder
from audio.alignment.swaps import SwapDetector
import logging

from audio.config import AudioSettings
from audio.lane_b.reviewer import PhoneticReviewer
from audio.passage import PassagePreparer
from audio.phonetics.g2p import build_g2p
from audio.pipeline import AudioPipeline, RowReviewerProtocol, WordLane
from audio.recognition.phoneme_recognizer import PhonemeRecognizer, Wav2Vec2PhonemeRecognizer
from audio.tagging.attempts import AttemptJudge
from audio.tagging.mispronunciation import MispronunciationTagger
from audio.tagging.moved import MovedWordTagger
from audio.tagging.post_pass import PostPass
from audio.tagging.reversal import ReversalTagger
from audio.tagging.scoring import ReadingScorer
from audio.transcription.crisper_whisper import CrisperWhisperTranscriber, TranscriberProtocol


def load_transcriber(settings: AudioSettings) -> CrisperWhisperTranscriber:
    """Builds + loads CrisperWhisper. Call once in the service lifespan, never per request."""
    transcriber = CrisperWhisperTranscriber(settings)
    transcriber.load()
    return transcriber


logger = logging.getLogger(__name__)


def load_phoneme_recognizer(settings: AudioSettings) -> Wav2Vec2PhonemeRecognizer | None:
    """Loads the Lane B phoneme model (warm-up included). Call once at startup, next to load_transcriber.

    None when AUDIO_PHONETIC_ENABLED is false: the service then runs on Lane A alone.
    """
    if not settings.PHONETIC_ENABLED:
        logger.warning("phonetic lane disabled (AUDIO_PHONETIC_ENABLED=false)")
        return None
    return Wav2Vec2PhonemeRecognizer(
        settings.PHONEME_MODEL,
        settings.PHONEME_DEVICE,
        settings.PHONEME_FP16,
        settings.PHONEME_CHUNK_S,
        settings.PHONEME_CONTEXT_S,
    )


def build_phonetic_reviewer(recogniser: PhonemeRecognizer, settings: AudioSettings) -> PhoneticReviewer:
    """Lane B composition: espeak G2P, one passage preparer (cached) and one for the ASR words (not)."""
    g2p = build_g2p(settings)
    return PhoneticReviewer(
        recogniser,
        PassagePreparer(g2p, settings.PASSAGE_CACHE_SIZE),
        PassagePreparer(g2p, cache_size=0),
        settings,
    )


def build_audio_pipeline(transcriber: TranscriberProtocol, reviewer: RowReviewerProtocol | None = None) -> AudioPipeline:
    """Composition root — the only place audio-pipeline concretes are constructed.

    reviewer = the Lane B PhoneticReviewer (build_phonetic_reviewer), or None to run Lane A alone.
    """
    labeler = WordLabeler()
    move_rule = MoveRule()
    lane_a = WordLane(
        cleaner=TranscriptCleaner(),
        aligner=MoveAwareAligner(NeedlemanWunschAligner(), move_rule),
        repeats=RepeatFolder(),
        swaps=SwapDetector(),
        off_text=OffTextDetector(),
        detector=Detector(labeler),
        moved=MovedWordTagger(move_rule),
    )
    post_pass = PostPass(AttemptJudge(), MispronunciationTagger(labeler), ReversalTagger())
    return AudioPipeline(
        transcriber=transcriber,
        lane_a=lane_a,
        post_pass=post_pass,
        scorer=ReadingScorer(),
        reviewers=(reviewer,) if reviewer else (),
    )
