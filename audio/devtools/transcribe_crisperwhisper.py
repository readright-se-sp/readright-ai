"""Transcribe all recordings in audio/data/ with nyralabs/CrisperWhisper2.0_large on GPU.

Caches word-level timestamps in devtools/out/crisperwhisper/<stem>.json.
"""

import gc
import json
import logging
import sys
import time
from pathlib import Path

import torch
from transformers import pipeline

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audio.recognition.audio_io import SAMPLE_RATE, load_wav  # noqa: E402

DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "devtools" / "out" / "crisperwhisper"
MODEL_ID = "nyralabs/CrisperWhisper2.0_large"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wavs = sorted(DATA_DIR.glob("*.wav"))
    if not wavs:
        logger.error("No WAV files found in %s", DATA_DIR)
        sys.exit(1)

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    logger.info("Initializing CrisperWhisper pipeline (%s) on %s (fp16)...", MODEL_ID, device)

    t0 = time.perf_counter()
    pipe = pipeline(
        "automatic-speech-recognition",
        model=MODEL_ID,
        torch_dtype=torch.float16 if "cuda" in device else torch.float32,
        device=device,
    )
    logger.info("CrisperWhisper 2.0 large loaded in %.1f s", time.perf_counter() - t0)

    for i, wav in enumerate(wavs, 1):
        out_json = OUT_DIR / f"{wav.stem}.json"
        logger.info("[%d/%d] Transcribing %s...", i, len(wavs), wav.name)
        t_wav = time.perf_counter()

        samples = load_wav(wav)
        result = pipe(
            {"raw": samples, "sampling_rate": SAMPLE_RATE},
            chunk_length_s=30,
            return_timestamps="word",
        )
        duration = time.perf_counter() - t_wav

        # Extract words with timestamps
        chunks = result.get("chunks", [])
        words = []
        for c in chunks:
            ts = c.get("timestamp", (None, None))
            words.append(
                {
                    "text": c.get("text", "").strip(),
                    "start": ts[0],
                    "end": ts[1],
                }
            )

        out_json.write_text(json.dumps(words, indent=2), encoding="utf-8")
        logger.info(
            "Transcribed %s: %d words, full_text_len=%d, time=%.2fs",
            wav.name,
            len(words),
            len(result.get("text", "")),
            duration,
        )

    del pipe
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    logger.info("All transcripts saved to %s", OUT_DIR)


if __name__ == "__main__":
    main()
