import math

import numpy as np
import pytest

from audio.recognition.ctc import (
    FRAME,
    collapse,
    frames_to_ms,
    n_frames_for,
    plan_chunks,
    stitch,
)

SR = 16000


@pytest.mark.parametrize("seconds", [0.5, 20, 20.01, 67])
def test_cores_cover_the_audio_exactly(seconds):
    n = round(seconds * SR)
    chunks = plan_chunks(n, SR, 20, 2)
    assert chunks[0].core_start == 0
    assert chunks[-1].core_end == n
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.core_end == b.core_start
    for c in chunks:
        assert c.core_start % FRAME == 0
        assert 0 <= c.in_start <= c.core_start < c.core_end <= c.in_end <= n
        assert c.core_start - c.in_start <= 2 * SR
        assert c.in_end - c.core_end <= 2 * SR
    assert len(chunks) == math.ceil(n / (20 * SR))


def test_empty_audio_has_no_chunks():
    assert plan_chunks(0, SR, 20, 2) == []


def fake_model_output(chunk):
    """Per-frame value = absolute frame index; frame count as a wav2vec2 conv stack emits it."""
    n_in = chunk.in_end - chunk.in_start
    frames = max(0, (n_in - 400) // FRAME + 1)
    first = chunk.in_start // FRAME
    return (first + np.arange(frames, dtype=np.float32))[:, None]


@pytest.mark.parametrize("seconds", [0.5, 20, 20.01, 67])
def test_stitch_takes_core_frames_from_the_right_chunk(seconds):
    n = round(seconds * SR)
    chunks = plan_chunks(n, SR, 20, 2)
    out = stitch([fake_model_output(c) for c in chunks], chunks)
    expected = n_frames_for(n)
    assert expected - 2 <= len(out) <= expected  # documented tolerance
    # every kept frame carries its absolute index, so boundaries come from the right chunk
    assert np.array_equal(out[:, 0], np.arange(len(out)))


def test_stitch_rejects_a_short_middle_chunk():
    chunks = plan_chunks(50 * SR, SR, 20, 2)
    outs = [fake_model_output(c) for c in chunks]
    outs[1] = outs[1][:-120]  # cuts into the core, not just the context
    with pytest.raises(ValueError):
        stitch(outs, chunks)


def lp(ids, vocab=4, p=0.9):
    out = np.full((len(ids), vocab), math.log((1 - p) / (vocab - 1)), dtype=np.float64)
    for t, i in enumerate(ids):
        out[t, i] = math.log(p)
    return out


def test_collapse_repeats_are_one_span():
    spans = collapse(lp([0, 1, 1, 1, 0, 2, 2, 0]), blank_id=0)
    assert [(s.token_id, s.start_frame, s.end_frame) for s in spans] == [(1, 1, 3), (2, 5, 6)]
    assert spans[0].confidence == pytest.approx(0.9)


def test_collapse_blank_separates_equal_ids():
    spans = collapse(lp([1, 1, 0, 1]), blank_id=0)
    assert [(s.token_id, s.start_frame, s.end_frame) for s in spans] == [(1, 0, 1), (1, 3, 3)]


def test_collapse_adjacent_different_ids_without_blank():
    spans = collapse(lp([1, 2]), blank_id=0)
    assert [s.token_id for s in spans] == [1, 2]


def test_collapse_all_blank_and_empty():
    assert collapse(lp([0, 0, 0]), blank_id=0) == []
    assert collapse(np.zeros((0, 4)), blank_id=0) == []


def test_collapse_last_frame_and_confidence_mean():
    arr = lp([0, 1, 1])
    arr[2] = [math.log(0.1), math.log(0.5), math.log(0.2), math.log(0.2)]
    spans = collapse(arr, blank_id=0)
    assert [(s.start_frame, s.end_frame) for s in spans] == [(1, 2)]
    assert spans[0].confidence == pytest.approx((0.9 + 0.5) / 2)


def test_times_use_the_20ms_grid_and_clip_to_the_audio():
    assert frames_to_ms(2, 4, 1000) == (40, 100)
    assert frames_to_ms(48, 49, 990) == (960, 990)
    assert frames_to_ms(60, 62, 990) == (990, 990)
