"""Articulatory feature distance in [0, 1], looked up in ``data/feature_distances.json``.

The table is generated at build time with panphon by ``devtools/build_feature_distances.py``;
panphon is not a runtime dependency. Rerun the generator when an unknown symbol is logged.
"""

import json
import logging
from functools import cache
from importlib import resources

logger = logging.getLogger(__name__)

_warned: set[str] = set()


@cache
def load_table() -> dict:
    text = (resources.files("audio.phonetics") / "data" / "feature_distances.json").read_text(
        encoding="utf-8"
    )
    return json.loads(text)


def feature_distance(a: str, b: str) -> float:
    if a == b:
        return 0.0
    table = load_table()
    symbols = set(table["symbols"])
    for sym in (a, b):
        if sym not in symbols and sym not in _warned:
            _warned.add(sym)
            logger.warning("no feature distance for %r; rerun build_feature_distances.py", sym)
    if a not in symbols or b not in symbols:
        return 1.0
    lo, hi = sorted((a, b))
    return table["distances"][lo][hi]
