"""Loader for ``data/accent_table.yaml`` (spec §6): cost constants and directional accent pairs."""

from functools import cache
from importlib import resources
from pathlib import Path
from typing import NamedTuple

import yaml

from audio.phonetics.ipa import INVENTORY

COST_KEYS = ("accent", "final_cluster_drop", "substitution_floor", "indel")


class AccentTable(NamedTuple):
    costs: dict[str, float]
    pairs: frozenset[tuple[str, str]]  # (expected, heard)


@cache
def load_accent_table(path: Path | None = None) -> AccentTable:
    if path is None:
        text = (resources.files("audio.phonetics") / "data" / "accent_table.yaml").read_text(
            encoding="utf-8"
        )
    else:
        text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)

    costs = raw.get("costs") or {}
    for key in COST_KEYS:
        value = costs.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ValueError(f"cost {key!r} must be a number in [0, 1], got {value!r}")

    pairs: list[tuple[str, str]] = []
    for item in raw.get("pairs") or []:
        if not (
            isinstance(item, list) and len(item) == 2 and all(isinstance(s, str) for s in item)
        ):
            raise ValueError(f"pair must be [expected, heard], got {item!r}")
        expected, heard = item
        if expected not in INVENTORY:
            raise ValueError(f"expected symbol {expected!r} is not in INVENTORY")
        if not heard or any(c.isspace() for c in heard):
            raise ValueError(f"heard symbol {heard!r} must be non-empty with no whitespace")
        pairs.append((expected, heard))
    if len(set(pairs)) != len(pairs):
        raise ValueError("duplicate accent pair")

    return AccentTable({k: float(costs[k]) for k in COST_KEYS}, frozenset(pairs))
