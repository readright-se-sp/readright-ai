import time

import numpy as np
import numpy.typing as npt

from audio.config import AudioSettings
from audio.flags.acoustics import hardly_audible, monotonous
from audio.models import AlignedWord, new_row
from audio.recognition.audio_io import SAMPLE_RATE

S = AudioSettings(_env_file=None)
WORD_S, GAP_S = 0.3, 0.3


def rows_for(n: int) -> list[AlignedWord]:
    out: list[AlignedWord] = []
    for i in range(n):
        start = 0.5 + i * (WORD_S + GAP_S)
        out.append(
            new_row(
                "match",
                passage_indices=[i],
                passage_words=["w"],
                spoken=["w"],
                start=start,
                end=start + WORD_S,
            )
        )
    return out


def amp(dbfs: float) -> float:
    """Peak amplitude of a sine whose RMS is ``dbfs``."""
    return float(10 ** (dbfs / 20) * np.sqrt(2))


def signal(
    n: int,
    freqs: list[float],
    *,
    speech_db: float = -20,
    noise_db: float | None = -60,
) -> npt.NDArray[np.float32]:
    total = 0.5 + n * (WORD_S + GAP_S) + 0.5
    wav = np.zeros(int(total * SAMPLE_RATE))
    for i, row in enumerate(rows_for(n)):
        assert row["start"] is not None
        a, b = int(row["start"] * SAMPLE_RATE), int((row["start"] + WORD_S) * SAMPLE_RATE)
        t = np.arange(b - a) / SAMPLE_RATE
        wav[a:b] = amp(speech_db) * np.sin(2 * np.pi * freqs[i % len(freqs)] * t)
    if noise_db is not None:
        rng = np.random.default_rng(0)
        wav += rng.normal(0, 10 ** (noise_db / 20), wav.size)
    return wav.astype(np.float32)


def test_constant_pitch_is_monotonous() -> None:
    out = monotonous(signal(20, [220.0]), rows_for(20), S)
    assert out.flag
    assert out.metrics["mono_pitch_sd_st"] is not None
    assert float(out.metrics["mono_pitch_sd_st"]) < 0.5  # type: ignore[arg-type]


def test_pitch_moving_four_semitones_is_not_monotonous() -> None:
    base = 220.0
    freqs = [base * 2 ** (st / 12) for st in (4, -4, 3, -3, 4, -4, 2, -2, 4, -4)]
    out = monotonous(signal(20, freqs), rows_for(20), S)
    assert not out.flag
    assert float(out.metrics["mono_pitch_sd_st"]) > 2.5  # type: ignore[arg-type]


def test_octave_jump_on_one_word_is_dropped() -> None:
    freqs = [220.0] * 19 + [440.0]
    wav = signal(20, freqs)
    out = monotonous(wav, rows_for(20), S)
    assert out.flag  # the jump would have raised the SD past 2 if it had been kept
    assert out.metrics["mono_words"] == 19


def test_too_few_voiced_words_is_insufficient() -> None:
    out = monotonous(signal(8, [220.0]), rows_for(8), S)
    assert not out.flag
    assert out.metrics["mono_reason"] == "insufficient"


def test_no_timed_words_is_insufficient() -> None:
    out = monotonous(signal(20, [220.0]), [], S)
    assert not out.flag
    assert out.metrics["mono_reason"] == "insufficient"


def test_clear_loud_speech_is_audible() -> None:
    out = hardly_audible(signal(10, [220.0], speech_db=-20, noise_db=-60), rows_for(10), S)
    assert not out.flag
    assert 38 < float(out.metrics["audible_snr_db"]) < 42  # type: ignore[arg-type]


def test_quiet_speech_is_hardly_audible() -> None:
    out = hardly_audible(signal(10, [220.0], speech_db=-45, noise_db=-80), rows_for(10), S)
    assert out.flag  # SNR is fine (35 dB); the level alone decides


def test_speech_close_to_noise_is_hardly_audible() -> None:
    out = hardly_audible(signal(10, [220.0], speech_db=-20, noise_db=-25), rows_for(10), S)
    assert out.flag  # loud enough, but only about 5 dB above the room


def test_no_gaps_uses_level_only() -> None:
    wav = signal(10, [220.0], noise_db=None)
    rows = rows_for(10)
    for r, nxt in zip(rows, rows[1:] + [rows[-1]], strict=False):
        r["end"] = nxt["start"]  # touching words, no silence between
    rows[0]["start"] = 0.0
    rows[-1]["end"] = len(wav) / SAMPLE_RATE
    out = hardly_audible(wav, rows, S)
    assert out.metrics["audible_reason"] == "no_noise_estimate"
    assert not out.flag


def test_digital_silence_is_hardly_audible() -> None:
    wav = np.zeros(SAMPLE_RATE * 10, dtype=np.float32)
    assert hardly_audible(wav, rows_for(10), S).flag


def test_sixty_second_signal_takes_under_a_second() -> None:
    n = 100
    wav = signal(n, [200.0, 260.0])
    rows = rows_for(n)
    start = time.perf_counter()
    monotonous(wav, rows, S)
    hardly_audible(wav, rows, S)
    assert time.perf_counter() - start < 1.0
