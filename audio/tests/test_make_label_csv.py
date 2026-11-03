"""Pairing and clip-span logic of devtools/make_label_csv.py (the cache test skips without it)."""

import pytest

from devtools.make_label_csv import clip_name, clip_span_ms, pair_rows
from devtools.replay import CACHE_DIR


def row(index, word, tag="match", start=1.0, end=1.5):
    return {
        "passage_indices": [index],
        "passage_word": word,
        "spoken_word": word,
        "start": start,
        "end": end,
        "tag": tag,
    }


def test_pair_rows_pairs_by_order():
    a = [row(0, "the"), row(1, "cat", "substitution")]
    b = [row(0, "the"), row(1, "cat", "match")]
    pairs, problems = pair_rows(a, b)
    assert problems == []
    assert [(p.row, p.lane_a["tag"], p.lane_b["tag"]) for p in pairs] == [
        (1, "match", "match"),
        (2, "substitution", "match"),
    ]


def test_pair_rows_reports_a_length_mismatch_and_a_position_mismatch():
    pairs, problems = pair_rows([row(0, "the"), row(1, "cat")], [row(0, "the"), row(2, "dog")])
    assert len(pairs) == 2
    assert len(problems) == 1 and "row 2" in problems[0]
    _, problems = pair_rows([row(0, "the")], [])
    assert "row count differs" in problems[0]


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (1.0, 1.5, (700, 1800)),
        (0.1, 0.4, (0, 700)),  # clamped at the start
        (9.8, 9.95, (9500, 10000)),  # clamped at the end
        (None, 1.0, None),  # untimed
        (2.0, None, None),
        (3.0, 3.0, (2700, 3300)),  # zero-length omission gap
    ],
)
def test_clip_span_ms(start, end, expected):
    assert clip_span_ms(start, end, 10000) == expected


def test_clip_name_is_filesystem_safe():
    assert clip_name(7, "Don't") == "0007_don_t.wav"
    assert clip_name(12, None) == "0012_x.wav"


@pytest.mark.espeak
@pytest.mark.skipif(not any(CACHE_DIR.glob("*.json")), reason="no replay cache (cache_runs.py)")
def test_cached_runs_pair_one_to_one(espeak_g2p):
    from audio.config import AudioSettings
    from devtools.replay import Replayer, load_all

    settings = AudioSettings(_env_file=None)
    replayer = Replayer(settings)
    for c in load_all():
        a = replayer.run(c, settings, phonetic=False)["words"]
        b = replayer.run(c, settings, phonetic=True)["words"]
        pairs, problems = pair_rows(a, b)
        assert problems == [] and len(pairs) == len(a)
