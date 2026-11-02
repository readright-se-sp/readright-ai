"""The rows hand-checked in Phase I1, replayed from the cache at the settings defaults (no models).

Skipped when the cache is missing: it holds student audio derivatives and is not in git
(`uv run python devtools/cache_runs.py` builds it). Rows marked `xfail: B6b` are known wrong.
"""

import functools
from pathlib import Path

import pytest
import yaml

from audio.config import AudioSettings

ROOT = Path(__file__).resolve().parents[1]
CASES = yaml.safe_load((ROOT / "tests" / "fixtures" / "handchecked_i1.yaml").read_text("utf-8"))
CACHE_DIR = ROOT / "devtools" / "out" / "cache"


def _param(case):
    marks = []
    if case.get("xfail"):
        marks.append(pytest.mark.xfail(reason=case["xfail"], strict=True))
    return pytest.param(case, marks=marks, id=f"{case['recording'][:10]}#{case['index']}")


@functools.cache
def _final_tags(stem: str) -> dict[int, str]:
    from devtools.replay import Replayer, load_cache

    path = next(CACHE_DIR.glob(f"{stem}*.json"))
    cached = load_cache(path)
    settings = AudioSettings(_env_file=None, PHONETIC_ENABLED=True)
    words = Replayer(settings).run(cached, settings)["words"]
    return {i: w["tag"] for w in words for i in w["passage_indices"][:1]}


@pytest.mark.espeak
@pytest.mark.parametrize("case", [_param(c) for c in CASES])
def test_handchecked_row(case, espeak_g2p):
    if not list(CACHE_DIR.glob(f"{case['recording']}*.json")):
        pytest.skip("no run cache in audio/devtools/out/cache (student audio is not in git)")
    assert _final_tags(case["recording"])[case["index"]] == case["expected"], case["note"]
