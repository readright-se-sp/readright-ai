import math

import numpy as np

from audio.recognition.ctc import Span, collapse
from audio.recognition.vocab import build_token_map, spans_to_tokens, unmapped_tokens

VOCAB = {"<pad>": 0, "|": 1, "ɛɹ": 2, "iː": 3, "tʃ": 4, "ø": 5, "<unk>": 6, "ˈa": 7}


def test_token_map():
    m = build_token_map(VOCAB)
    assert m[0] == () and m[1] == () and m[6] == ()
    assert m[2] == ("ɛ", "ɹ")
    assert m[3] == ("i",)
    assert m[4] == ("tʃ",)
    assert m[5] == ("ø",)
    assert m[7] == ("a",)


def test_extra_special_tokens_are_dropped():
    assert build_token_map({"<foo>": 0}, ["<foo>"]) == {0: ()}


def test_unmapped_tokens_lists_non_inventory_segments():
    assert unmapped_tokens(build_token_map(VOCAB)) == {5: ("ø",)}


def test_multi_segment_span_shares_frames():
    m = build_token_map(VOCAB)
    toks = spans_to_tokens([Span(2, 10, 13, 0.8)], m, audio_ms=10_000)
    assert [(t.symbol, t.start_ms, t.end_ms, t.confidence) for t in toks] == [
        ("ɛ", 200, 240, 0.8),
        ("ɹ", 240, 280, 0.8),
    ]


def test_one_frame_span_with_two_segments_gets_a_frame_each():
    m = build_token_map(VOCAB)
    toks = spans_to_tokens([Span(2, 5, 5, 0.7)], m, audio_ms=10_000)
    assert [(t.symbol, t.start_ms, t.end_ms) for t in toks] == [("ɛ", 100, 120), ("ɹ", 120, 140)]


def test_dropped_and_sorted_output():
    m = build_token_map(VOCAB)
    spans = [Span(1, 0, 1, 0.9), Span(4, 2, 3, 0.9), Span(3, 5, 5, 0.9)]
    toks = spans_to_tokens(spans, m, audio_ms=1000)
    assert [t.symbol for t in toks] == ["tʃ", "i"]


def test_end_to_end_from_logprobs():
    lp = np.full((6, 8), math.log(0.01))
    for t, i in enumerate([0, 4, 4, 0, 3, 0]):
        lp[t, i] = math.log(0.9)
    toks = spans_to_tokens(collapse(lp, 0), build_token_map(VOCAB), audio_ms=120)
    assert [(t.symbol, t.start_ms, t.end_ms) for t in toks] == [("tʃ", 20, 60), ("i", 80, 100)]
