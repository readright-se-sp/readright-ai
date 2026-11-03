import pytest

from audio.config import AudioSettings
from audio.flags import compute_flags
from audio.flags.fluency import FlagOutcome
from tests.flags.test_acoustics import rows_for, signal

S = AudioSettings(_env_file=None)
TEXT = "w " * 20


def test_flat_quiet_reading_sets_the_acoustic_flags() -> None:
    wav = signal(20, [220.0], speech_db=-45, noise_db=-80)
    out = compute_flags(wav, rows_for(20), TEXT, S)
    assert out.monotonous
    assert out.hardly_audible
    assert out.metrics["audible_speech_dbfs"] is not None


def test_disabled_gives_all_false() -> None:
    wav = signal(20, [220.0], speech_db=-45)
    off = S.model_copy(update={"FLAGS_ENABLED": False})
    out = compute_flags(wav, rows_for(20), TEXT, off)
    assert not (out.word_by_word or out.disregard_punctuation or out.monotonous)
    assert not out.hardly_audible
    assert out.metrics == {}


def test_a_raising_flag_is_false_and_the_others_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: object) -> FlagOutcome:
        raise ValueError("boom")

    monkeypatch.setattr("audio.flags.monotonous", boom)
    wav = signal(20, [220.0], speech_db=-45, noise_db=-80)
    out = compute_flags(wav, rows_for(20), TEXT, S)
    assert not out.monotonous
    assert out.hardly_audible
    assert out.metrics["monotonous_error"] == "failed"


def test_bad_rows_never_raise() -> None:
    wav = signal(5, [220.0])
    out = compute_flags(wav, [], "", S)
    assert not (out.word_by_word or out.disregard_punctuation or out.monotonous)
