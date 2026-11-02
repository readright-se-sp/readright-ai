"""Replay from the cache (devtools/replay.py): same code path as a live run, no models."""

import pytest

from audio.config import AudioSettings
from audio.types import PhonemeToken
from devtools.replay import CACHE_DIR, Cached, Replayer, load_all, with_overrides

pytestmark = pytest.mark.espeak


def synthetic() -> Cached:
    words = [
        {"text": "the", "start": 0.0, "end": 0.3, "score": 0.9},
        {"text": "cat", "start": 0.4, "end": 0.8, "score": 0.9},
    ]
    toks = (
        PhonemeToken(symbol="ð", start_ms=0, end_ms=100, confidence=0.9),
        PhonemeToken(symbol="ə", start_ms=100, end_ms=250, confidence=0.9),
        PhonemeToken(symbol="k", start_ms=400, end_ms=500, confidence=0.9),
        PhonemeToken(symbol="æ", start_ms=500, end_ms=650, confidence=0.9),
        PhonemeToken(symbol="t", start_ms=650, end_ms=780, confidence=0.9),
    )
    return Cached("rec", "p", "The cat", words, [], toks, {})  # type: ignore[arg-type]


def test_replay_reviews_with_the_cached_tokens(espeak_g2p):
    replayer = Replayer(AudioSettings(_env_file=None))
    rows = replayer.run(synthetic())["words"]
    assert [r["tag"] for r in rows] == ["match", "match"]
    assert [r["phonetic"]["check"] for r in rows] == ["done", "done"]
    assert rows[1]["phonetic"]["heard_phonemes"] == ["k", "æ", "t"]


def test_replay_without_phonetic_is_lane_a_only(espeak_g2p):
    rows = Replayer(AudioSettings(_env_file=None)).run(synthetic(), phonetic=False)["words"]
    assert all("phonetic" not in r for r in rows)


def test_settings_overrides_reach_the_reviewer(espeak_g2p):
    base = AudioSettings(_env_file=None)
    assert with_overrides(base, JOIN_MAX_REACH_MS=100).JOIN_MAX_REACH_MS == 100
    assert with_overrides(base, JOIN_MAX_REACH_MS=100).T_FLAG == base.T_FLAG


@pytest.mark.skipif(
    not list(CACHE_DIR.glob("*.json")), reason="no cache (run devtools/cache_runs.py)"
)
def test_real_cache_replays_to_the_cached_lane_a_rows(espeak_g2p):
    replayer = Replayer(AudioSettings(_env_file=None))
    for c in load_all():
        words = replayer.run(c, phonetic=False)["words"]
        assert len(words) == len(c.lane_a_rows)
