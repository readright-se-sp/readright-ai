from audio.alignment.cleanup import TranscriptCleaner
from audio.alignment.detection import Detector
from audio.alignment.labels import WordLabeler
from audio.alignment.moves import MoveAwareAligner, MoveRule
from audio.alignment.needleman_wunsch import NeedlemanWunschAligner
from audio.alignment.off_text import OffTextDetector
from audio.alignment.repeats import RepeatFolder
from audio.alignment.swaps import SwapDetector
from audio.config import AudioSettings
from audio.pipeline import AudioPipeline, WordLane
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


def build_audio_pipeline(transcriber: TranscriberProtocol) -> AudioPipeline:
    """Composition root — the only place audio-pipeline concretes are constructed."""
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
    return AudioPipeline(transcriber=transcriber, lane_a=lane_a, post_pass=post_pass, scorer=ReadingScorer())
