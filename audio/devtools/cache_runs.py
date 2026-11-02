"""Run the slow models once per recording and cache the results (git-ignored).

For each WAV in audio/data/: CrisperWhisper words (Lane A's transcriber), Lane A's rows before
review,
and the phoneme tokens (Lane B's recogniser). Saved to devtools/out/cache/<recording>.json. Cached
entries stay valid until the model ids change; --force re-runs everything.

    uv run python devtools/cache_runs.py [file.wav ...] [--force]
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from recordings import PASSAGE_NAME_BY_PREFIX, passage_for  # noqa: E402
from replay import CACHE_DIR, model_ids  # noqa: E402

from audio.alignment.text import guess_proper_nouns, normalize, tokenize_passage  # noqa: E402
from audio.config import AudioSettings  # noqa: E402
from audio.wiring import build_audio_pipeline, load_phoneme_recognizer, load_transcriber  # noqa: E402

DATA = ROOT / "data"
logger = logging.getLogger("cache_runs")


def passage_id(wav: Path) -> str:
    matches = [p for p in PASSAGE_NAME_BY_PREFIX if wav.name.startswith(p)]
    return PASSAGE_NAME_BY_PREFIX[max(matches, key=len)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="*", help="WAV names or paths (default: all of audio/data/)")
    parser.add_argument("--force", action="store_true", help="re-run even when the cache is valid")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    wavs = [Path(w) if Path(w).is_absolute() else DATA / w for w in args.wav] or sorted(
        DATA.glob("*.wav")
    )
    if not wavs:
        sys.exit(f"No WAV files in {DATA}")
    settings = AudioSettings()
    models = model_ids(settings)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    todo = []
    for wav in wavs:
        out = CACHE_DIR / f"{wav.stem}.json"
        if not args.force and out.exists():
            if json.loads(out.read_text(encoding="utf-8")).get("models") == models:
                logger.info("cached: %s", wav.name)
                continue
            logger.info("model ids changed, re-running %s", wav.name)
        todo.append(wav)
    if not todo:
        return

    transcriber = load_transcriber(settings)
    recogniser = load_phoneme_recognizer(settings)
    assert recogniser is not None, "AUDIO_PHONETIC_ENABLED must be true to cache phoneme tokens"
    lane_a = build_audio_pipeline(transcriber)  # to reach Lane A's rows before review

    for wav in todo:
        passage = passage_for(wav)
        t0 = time.perf_counter()
        spoken = transcriber.transcribe(str(wav))
        asr_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        tokens = recogniser.recognise(wav, None)
        rec_s = time.perf_counter() - t0
        names = guess_proper_nouns(passage)
        nouns = frozenset(w for n in names for w in normalize(n))
        rows = lane_a._lane_a.rows(spoken, tokenize_passage(passage), nouns)  # noqa: SLF001
        entry = {
            "recording": wav.stem,
            "passage_id": passage_id(wav),
            "passage": passage,
            "spoken": spoken,
            "lane_a_rows": rows,
            "phoneme_tokens": [t.model_dump() for t in tokens],
            "models": models,
            "timing_s": {"asr": round(asr_s, 2), "recogniser": round(rec_s, 2)},
        }
        (CACHE_DIR / f"{wav.stem}.json").write_text(
            json.dumps(entry, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("cached %s (asr %.1fs, recogniser %.1fs)", wav.name, asr_s, rec_s)


if __name__ == "__main__":
    main()
