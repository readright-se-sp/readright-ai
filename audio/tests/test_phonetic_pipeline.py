"""AudioPipeline with and without the Lane B reviewer (fake ASR and recogniser, no models)."""

import logging

from audio.config import AudioSettings
from audio.lane_b.reviewer import PhoneticReviewer
from audio.models import CONTRACT_VERSION
from audio.passage import PassagePreparer
from audio.types import PhonemeToken
from audio.wiring import build_audio_pipeline, load_phoneme_recognizer
from tests.conftest import FakeG2P, FakeTranscriber, spoken

TABLE = {
    "the": ("ð", "ə"),
    "cat": ("k", "æ", "t"),
    "cot": ("k", "ɒ", "t"),
    "sat": ("s", "æ", "t"),
    "dog": ("d", "ɑ", "g"),
}
TEXT = "the cat sat"

# Tuning values pinned here so these tests stay about behaviour, not about the B6a defaults.
TEST_SETTINGS = AudioSettings(
    _env_file=None, COVERAGE_GUARD_RATIO=0.5, SHORT_WORD_MAX_PHONES=0, OVERRULE_MATCH=True
)


class FakeRecogniser:
    def __init__(self, tokens=(), exc=None):
        self.tokens, self.exc = tuple(tokens), exc

    def recognise(self, wav_path, deadline_ms):
        if self.exc:
            raise self.exc
        return self.tokens


def tokens_for(words, step_ms=400):
    """Phonemes of each word, inside the window FakeTranscriber gives it (400 ms apart)."""
    out = []
    for i, word in enumerate(words):
        for j, sym in enumerate(TABLE[word]):
            start = i * step_ms + j * 90
            out.append(PhonemeToken(symbol=sym, start_ms=start, end_ms=start + 60, confidence=0.9))
    return out


def reviewer(recogniser):
    return PhoneticReviewer(
        recogniser,
        PassagePreparer(FakeG2P(TABLE)),
        PassagePreparer(FakeG2P(TABLE), cache_size=0),
        TEST_SETTINGS,
    )


def run(heard_text, recogniser=None):
    rev = reviewer(recogniser) if recogniser else None
    pipeline = build_audio_pipeline(FakeTranscriber(heard_text), rev)
    return pipeline.run("a.wav", TEXT)


def test_contract_version_is_6():
    assert CONTRACT_VERSION == "7"
    assert run(TEXT)["version"] == "7"


def test_without_a_reviewer_the_output_equals_analyze_and_has_no_phonetic_field():
    pipeline = build_audio_pipeline(FakeTranscriber(TEXT))
    result = pipeline.run("a.wav", TEXT)
    assert result == pipeline.analyze(spoken(TEXT), TEXT)
    assert all("phonetic" not in w for w in result["words"])


def test_judged_rows_carry_evidence_when_everything_agrees():
    result = run(TEXT, FakeRecogniser(tokens_for(["the", "cat", "sat"])))
    assert [w["tag"] for w in result["words"]] == ["match"] * 3
    assert [w["phonetic"]["check"] for w in result["words"]] == ["done"] * 3
    assert result["score"]["error_count"] == 0


def test_lane_b_catches_a_word_the_asr_wrote_right_but_the_student_said_wrong():
    heard = tokens_for(["the", "dog", "sat"])  # the ASR says "cat"; the sound is "dog"
    result = run(TEXT, FakeRecogniser(heard))
    assert [w["tag"] for w in result["words"]] == ["match", "mispronunciation", "match"]
    assert result["score"]["error_count"] == 1


def test_a_judged_substitution_is_not_re_tagged_by_spelling():
    # The ASR wrote "cot" (close to "cat" in spelling, so the letter-based tagger alone says
    # mispronunciation), but the sound is a clean "dog" and "cot" is G2P'd as "dog" here:
    # the heard sound is nearer to the ASR word than to "cat", so Lane B says substitution.
    table = {**TABLE, "cot": TABLE["dog"]}
    without_b = run("the cot sat")
    assert without_b["words"][1]["tag"] == "mispronunciation"
    rev = PhoneticReviewer(
        FakeRecogniser(tokens_for(["the", "dog", "sat"])),
        PassagePreparer(FakeG2P(table)),
        PassagePreparer(FakeG2P(table), cache_size=0),
        TEST_SETTINGS,
    )
    result = build_audio_pipeline(FakeTranscriber("the cot sat"), rev).run("a.wav", TEXT)
    row = result["words"][1]
    assert (row["tag"], row["phonetic"]["check"]) == ("substitution", "done")


def test_a_close_sound_forgives_a_misspelt_word():
    # "cot" for "cat": ɒ for æ is a small step (d_exp is below T_FLAG), so Lane B calls it right
    row = run("the cot sat", FakeRecogniser(tokens_for(["the", "cot", "sat"])))["words"][1]
    assert (row["tag"], row["counted"], row["phonetic"]["check"]) == ("match", False, "done")


def test_a_failing_recogniser_gives_exactly_the_lane_a_result(caplog):
    with caplog.at_level(logging.WARNING):
        broken = run(TEXT, FakeRecogniser(exc=RuntimeError("boom")))
    assert broken == run(TEXT)
    assert "phonetic_check=skipped" in caplog.text


def test_phonetic_disabled_loads_no_model(caplog):
    with caplog.at_level(logging.WARNING):
        assert (
            load_phoneme_recognizer(AudioSettings(PHONETIC_ENABLED=False, _env_file=None)) is None
        )
    assert "disabled" in caplog.text
