"""Chunk planning, stitching and greedy CTC collapse. Pure numpy: no torch import.

Frame grid: wav2vec2 emits one frame per 320 samples (20 ms at 16 kHz). Frame ``i`` starts at
sample ``320 * i``.
"""

import math
from dataclasses import dataclass

import numpy as np

FRAME = 320  # samples per output frame
FRAME_MS = 20
# A model's conv stack emits floor((n - 400) / 320) + 1 frames for n samples, up to 2 fewer than
# ceil(n / 320). ``stitch`` tolerates that shortfall at the end of the last chunk only.
FRAME_TOLERANCE = 2


@dataclass(frozen=True)
class Chunk:
    in_start: int  # input span, samples (core widened by the context, clipped to the audio)
    in_end: int
    core_start: int  # core span, samples; starts are multiples of FRAME
    core_end: int


@dataclass(frozen=True)
class Span:
    token_id: int
    start_frame: int
    end_frame: int  # inclusive
    confidence: float


def plan_chunks(n_samples: int, sr: int, chunk_s: float, context_s: float) -> list[Chunk]:
    """Cores cover ``[0, n_samples)`` exactly, without overlap, on frame boundaries."""
    if n_samples <= 0:
        return []
    core_len = max(FRAME, round(chunk_s * sr / FRAME) * FRAME)
    context = round(context_s * sr / FRAME) * FRAME
    chunks = []
    for core_start in range(0, n_samples, core_len):
        core_end = min(core_start + core_len, n_samples)
        chunks.append(
            Chunk(
                max(0, core_start - context),
                min(n_samples, core_end + context),
                core_start,
                core_end,
            )
        )
    return chunks


def n_frames_for(n_samples: int) -> int:
    return math.ceil(n_samples / FRAME)


def stitch(chunk_logprobs: list[np.ndarray], chunks: list[Chunk]) -> np.ndarray:
    """Keep each chunk's core frames and concatenate them into ``[T, V]``.

    ``T`` is ``ceil(n_samples / 320)`` minus a shortfall of at most ``FRAME_TOLERANCE`` frames,
    which only the end of the last chunk may have (the model's conv stack emits slightly fewer
    frames than ``ceil(n / 320)``). Any other shortfall raises ``ValueError``.
    """
    if len(chunk_logprobs) != len(chunks) or not chunks:
        raise ValueError("need one log-prob array per chunk")
    parts = []
    for i, (lp, chunk) in enumerate(zip(chunk_logprobs, chunks, strict=True)):
        first = (chunk.core_start - chunk.in_start) // FRAME
        last = first + n_frames_for(chunk.core_end - chunk.core_start)  # exclusive
        part = lp[first:last]
        missing = last - first - len(part)
        if missing and (missing > FRAME_TOLERANCE or i != len(chunks) - 1):
            raise ValueError(f"chunk {i} has {len(lp)} frames, needs {last}")
        parts.append(part)
    return np.concatenate(parts, axis=0)


def collapse(logprobs: np.ndarray, blank_id: int) -> list[Span]:
    """Greedy CTC: argmax per frame; a run of one non-blank id is a span; a blank ends a run."""
    if logprobs.shape[0] == 0:
        return []
    ids = logprobs.argmax(axis=1)
    probs = np.exp(logprobs[np.arange(len(ids)), ids])
    spans: list[Span] = []
    start = 0
    for t in range(1, len(ids) + 1):
        if t == len(ids) or ids[t] != ids[start]:
            if ids[start] != blank_id:
                spans.append(Span(int(ids[start]), start, t - 1, float(probs[start:t].mean())))
            start = t
    return spans


def frames_to_ms(start_frame: int, end_frame: int, audio_ms: int) -> tuple[int, int]:
    """``start_ms = start * 20``, ``end_ms = (end + 1) * 20``, both clipped to the audio."""
    end_ms = min((end_frame + 1) * FRAME_MS, audio_ms)
    return min(start_frame * FRAME_MS, end_ms), end_ms
