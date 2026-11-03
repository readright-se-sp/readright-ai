"""is_monotonous and is_hardly_audible, from the WAV samples and the word times.

Pitch comes from Praat (parselmouth). Not enough evidence gives ``False`` with the reason
``"insufficient"``.
"""

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import parselmouth  # pyright: ignore[reportMissingTypeStubs]

from audio.config import AudioSettings
from audio.flags.fluency import READ_TAGS, FlagOutcome
from audio.models import AlignedWord
from audio.recognition.audio_io import SAMPLE_RATE

praat: Any = parselmouth  # untyped library: keep its objects as Any

Samples = npt.NDArray[np.float32]
Span = tuple[float, float]

PITCH_STEP_S = 0.01
NOISE_FRAME_S = 0.05
NOISE_MIN_GAP_S = 0.2
NOISE_PERCENTILE = 10
OCTAVE_ERROR_ST = 11.0  # an octave jump measures about 12; the margin absorbs tracker wobble
_FLOOR_DB = -120.0  # digital silence


def read_spans(rows: Sequence[AlignedWord]) -> list[Span]:
    """(start, end) seconds of every timed row that was read, sorted."""
    spans = [
        (r["start"], r["end"])
        for r in rows
        if r["tag"] in READ_TAGS and r["start"] is not None and r["end"] is not None
    ]
    return sorted((s, e) for s, e in spans if e > s)


def _all_spans(rows: Sequence[AlignedWord]) -> list[Span]:
    """Every timed row, any tag: where something was said, so it isn't a noise gap."""
    spans = [
        (r["start"], r["end"]) for r in rows if r["start"] is not None and r["end"] is not None
    ]
    return sorted((s, e) for s, e in spans if e > s)


def _db(rms: float) -> float:
    return max(_FLOOR_DB, 20 * math.log10(rms)) if rms > 0 else _FLOOR_DB


def _slice(wav: Samples, span: Span) -> Samples:
    return wav[max(0, int(span[0] * SAMPLE_RATE)) : max(0, int(span[1] * SAMPLE_RATE))]


def monotonous(wav: Samples, rows: Sequence[AlignedWord], settings: AudioSettings) -> FlagOutcome:
    """True when the pitch SD across words is under ``MONO_MAX_ST`` semitones.

    Median F0 per word (voiced frames only), converted to semitones from the reader's median.
    Words about an octave or more from that median are tracker octave errors and are dropped.
    """
    metrics: dict[str, float | int | str | None] = {"mono_words": 0, "mono_pitch_sd_st": None}
    spans = read_spans(rows)
    if not spans:
        metrics["mono_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    sound = praat.Sound(wav.astype(np.float64), sampling_frequency=SAMPLE_RATE)
    pitch = sound.to_pitch_ac(
        time_step=PITCH_STEP_S,
        pitch_floor=settings.PITCH_FLOOR_HZ,
        pitch_ceiling=settings.PITCH_CEIL_HZ,
    )
    f0: npt.NDArray[np.float64] = np.asarray(pitch.selected_array["frequency"], dtype=np.float64)
    times: npt.NDArray[np.float64] = np.asarray(pitch.xs(), dtype=np.float64)
    medians: list[float] = []
    for start, end in spans:
        voiced = f0[(times >= start) & (times <= end) & (f0 > 0)]
        if voiced.size:
            medians.append(float(np.median(voiced)))
    if not medians:
        metrics["mono_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    semitones = 12 * np.log2(np.asarray(medians) / float(np.median(medians)))
    kept = semitones[np.abs(semitones) <= OCTAVE_ERROR_ST]
    metrics["mono_words"] = int(kept.size)
    metrics["mono_median_hz"] = float(np.median(medians))
    if kept.size < settings.FLAGS_MIN_WORDS:
        metrics["mono_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    sd = float(np.std(kept))
    metrics["mono_pitch_sd_st"] = sd
    return FlagOutcome(sd < settings.MONO_MAX_ST, metrics)


def _noise_db(wav: Samples, rows: Sequence[AlignedWord]) -> float | None:
    """Quiet level of the room: 10th percentile of 50 ms frame levels in the word-free gaps."""
    total = wav.size / SAMPLE_RATE
    frame = int(NOISE_FRAME_S * SAMPLE_RATE)
    levels: list[float] = []
    merged: list[Span] = []
    for start, end in _all_spans(rows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    cursor = 0.0
    gaps: list[Span] = []
    for start, end in merged:
        gaps.append((cursor, start))
        cursor = max(cursor, end)
    gaps.append((cursor, total))
    for gap in gaps:
        if gap[1] - gap[0] < NOISE_MIN_GAP_S:
            continue
        part = _slice(wav, gap)
        for i in range(0, part.size - frame + 1, frame):
            levels.append(
                _db(float(np.sqrt(np.mean(np.square(part[i : i + frame], dtype=np.float64)))))
            )
    return float(np.percentile(levels, NOISE_PERCENTILE)) if levels else None


def hardly_audible(
    wav: Samples, rows: Sequence[AlignedWord], settings: AudioSettings
) -> FlagOutcome:
    """True when the voice is quiet (< ``AUDIBLE_MIN_DBFS``) or barely above the room noise."""
    metrics: dict[str, float | int | str | None] = {
        "audible_speech_dbfs": None,
        "audible_noise_dbfs": None,
        "audible_snr_db": None,
    }
    spans = read_spans(rows)
    if not spans:
        metrics["audible_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    speech = np.concatenate([_slice(wav, s) for s in spans]).astype(np.float64)
    if speech.size == 0:
        metrics["audible_reason"] = "insufficient"
        return FlagOutcome(False, metrics)
    speech_db = _db(float(np.sqrt(np.mean(np.square(speech)))))
    noise_db = _noise_db(wav, rows)
    metrics["audible_speech_dbfs"] = speech_db
    quiet = speech_db < settings.AUDIBLE_MIN_DBFS
    noisy = False
    if noise_db is None:
        metrics["audible_reason"] = "no_noise_estimate"  # level alone decides
    else:
        snr = speech_db - noise_db
        metrics["audible_noise_dbfs"] = noise_db
        metrics["audible_snr_db"] = snr
        noisy = snr < settings.AUDIBLE_MIN_SNR_DB
    return FlagOutcome(quiet or noisy, metrics)
