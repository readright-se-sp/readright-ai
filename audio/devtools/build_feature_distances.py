"""Generate ``src/audio/phonetics/data/feature_distances.json`` (B4, build time only).

panphon is NOT a project dependency (it fails to import on Windows with the cp1252 default and
pulls in pandas). Run from ``audio/``:

    PYTHONUTF8=1 uv run --with panphon python devtools/build_feature_distances.py

(PowerShell: ``$env:PYTHONUTF8=1`` first.) Needs the recogniser's tokenizer in the Hugging Face
cache. Commit the JSON; rerun when the recogniser shows a symbol the table does not have.
"""

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audio.config import AudioSettings  # noqa: E402
from audio.phonetics.ipa import INVENTORY  # noqa: E402
from audio.recognition.vocab import build_token_map  # noqa: E402

OUT = ROOT / "src" / "audio" / "phonetics" / "data" / "feature_distances.json"

# symbol -> a spelling panphon knows (filled in from the "not recognised" report)
_ALIASES: dict[str, str] = {"ɚ": "ə˞"}


def recogniser_symbols() -> set[str]:
    # vocab.json is the tokenizer's vocabulary; reading it avoids the tokenizer's espeak need.
    from huggingface_hub import hf_hub_download

    vocab = json.loads(
        Path(hf_hub_download(AudioSettings().PHONEME_MODEL, "vocab.json")).read_text("utf-8")
    )
    token_map = build_token_map(vocab)  # <pad> <s> </s> <unk> | are DEFAULT_SPECIAL
    return {s for seq in token_map.values() for s in seq}


def main() -> None:
    from panphon import FeatureTable
    from panphon.distance import Distance

    ft, dist = FeatureTable(), Distance()
    symbols = sorted(set(INVENTORY) | recogniser_symbols())

    def spelling(sym: str) -> str | None:
        for cand in (sym, _ALIASES.get(sym)):
            if cand and "".join(ft.ipa_segs(cand)) == cand:
                return cand
        return None

    known = {s: sp for s in symbols if (sp := spelling(s))}
    left_out = [s for s in symbols if s not in known]

    def raw(a: str, b: str) -> float:
        return dist.weighted_feature_edit_distance(known[a], known[b])

    singles = [s for s in INVENTORY if len(s) == 1]
    scale_r = max(raw(a, b) for a, b in itertools.combinations(sorted(singles), 2))

    distances: dict[str, dict[str, float]] = {}
    for a, b in itertools.combinations(sorted(known), 2):  # a < b by code point
        distances.setdefault(a, {})[b] = round(min(1.0, raw(a, b) / scale_r), 4)

    data = {"scale_R": scale_r, "symbols": sorted(known), "distances": distances}
    OUT.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=0) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes), {len(known)} symbols, scale_R={scale_r}")
    print("aliases used:", {s: sp for s, sp in known.items() if s != sp} or "none")
    print("left out:", left_out or "none")


if __name__ == "__main__":
    main()
