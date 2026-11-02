"""Run the full AudioPipeline (CrisperWhisper + Lane A + Lane B) on the recordings in audio/data/.

For each recording it runs Lane A alone and then Lane B on top, on the same transcript, and writes
the per-row Lane A tag vs final tag with d_exp, d_hyp and the heard IPA, plus timing for ASR, the
recogniser and the whole request, and peak GPU memory. Output: devtools/out/pipeline/ (git-ignored).
Not calibration: thresholds stay at the settings defaults.

    uv run python devtools/run_pipeline.py [file.wav ...] [--no-phonetic]
"""

import argparse
import json
import logging
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from recordings import passage_for  # noqa: E402

from audio.config import AudioSettings  # noqa: E402
from audio.models import AlignedWord, SpokenWord  # noqa: E402
from audio.wiring import (  # noqa: E402
    build_audio_pipeline,
    build_phonetic_reviewer,
    load_phoneme_recognizer,
    load_transcriber,
)

DATA = ROOT / "data"
OUT = ROOT / "devtools" / "out" / "pipeline"
logger = logging.getLogger("run_pipeline")


class VramSampler:
    """Peak device memory in use (all processes, CUDA allocators of torch and CTranslate2 alike)."""

    def __init__(self) -> None:
        import torch

        self._torch = torch
        self._stop = threading.Event()
        self.baseline = self.peak = self._used()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _used(self) -> int:
        free, total = self._torch.cuda.mem_get_info()
        return total - free

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            self.peak = max(self.peak, self._used())

    def __enter__(self) -> "VramSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def reset(self) -> None:
        self.peak = self._used()

    @property
    def peak_mb(self) -> float:
        return self.peak / 2**20


class ReplayTranscriber:
    """Hands back a transcript that was already computed, so both runs share one ASR pass."""

    def __init__(self) -> None:
        self.words: list[SpokenWord] = []

    def transcribe(self, wav_path: str) -> list[SpokenWord]:
        return self.words


def _row_view(a: AlignedWord, b: AlignedWord) -> dict[str, object]:
    ph = b.get("phonetic")
    return {
        "passage_indices": b["passage_indices"],
        "passage_word": b["passage_word"],
        "spoken_word": b["spoken_word"],
        "start": b["start"],
        "end": b["end"],
        "lane_a_tag": a["tag"],
        "final_tag": b["tag"],
        "changed": a["tag"] != b["tag"],
        "check": ph["check"] if ph else None,
        "d_exp": ph["d_exp"] if ph else None,
        "d_hyp": ph["d_hyp"] if ph else None,
        "expected_ipa": " ".join(ph["expected_phonemes"]) if ph else None,
        "heard_ipa": " ".join(ph["heard_phonemes"]) if ph else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="*", help="WAV names or paths (default: all of audio/data/)")
    parser.add_argument("--no-phonetic", action="store_true", help="Lane A only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    wavs = [Path(w) if Path(w).is_absolute() else DATA / w for w in args.wav] or sorted(
        DATA.glob("*.wav")
    )
    if not wavs:
        sys.exit(f"No WAV files in {DATA}")

    settings = AudioSettings(PHONETIC_ENABLED=not args.no_phonetic)
    import torch

    cuda = torch.cuda.is_available()
    with VramSampler() if cuda else _NoVram() as vram:
        t0 = time.perf_counter()
        transcriber = load_transcriber(settings)
        asr_load_s = time.perf_counter() - t0
        asr_vram = vram.peak_mb - vram.baseline / 2**20
        t0 = time.perf_counter()
        recogniser = None if args.no_phonetic else load_phoneme_recognizer(settings)
        rec_load_s = time.perf_counter() - t0
        reviewer = build_phonetic_reviewer(recogniser, settings) if recogniser else None
        replay = ReplayTranscriber()
        lane_a_only = build_audio_pipeline(replay)
        full = build_audio_pipeline(replay, reviewer)
        loaded_mb = vram._used() / 2**20 if cuda else 0.0  # noqa: SLF001
        print(
            f"loaded: ASR {asr_load_s:.1f}s, recogniser {rec_load_s:.1f}s, "
            f"GPU in use {loaded_mb:.0f} MB"
        )

        OUT.mkdir(parents=True, exist_ok=True)
        changes: Counter[str] = Counter()
        summary: list[dict[str, object]] = []
        for wav in wavs:
            passage = passage_for(wav)
            vram.reset() if cuda else None
            t0 = time.perf_counter()
            replay.words = transcriber.transcribe(str(wav))
            asr_s = time.perf_counter() - t0
            base = lane_a_only.run(str(wav), passage)
            t0 = time.perf_counter()
            final = full.run(str(wav), passage)
            lane_b_s = time.perf_counter() - t0
            timing = reviewer.last_timing_ms if reviewer else {}
            assert len(base["words"]) == len(final["words"])
            rows = [_row_view(a, b) for a, b in zip(base["words"], final["words"], strict=True)]
            for r in rows:
                if r["changed"]:
                    changes[f"{r['lane_a_tag']} -> {r['final_tag']}"] += 1
            peak_mb = vram.peak_mb if cuda else 0.0
            info: dict[str, object] = {
                "recording": wav.name,
                "rows": len(rows),
                "changed": sum(bool(r["changed"]) for r in rows),
                "asr_s": round(asr_s, 2),
                "recogniser_s": round(timing.get("recognise_ms", 0) / 1000, 2),
                "lane_b_total_s": round(timing.get("total_ms", 0) / 1000, 2),
                "pipeline_after_asr_s": round(lane_b_s, 2),
                "total_s": round(asr_s + lane_b_s, 2),
                "peak_gpu_mb": round(peak_mb),
                "errors_lane_a": base["score"]["error_count"],
                "errors_final": final["score"]["error_count"],
                "wpm": final["score"]["wpm"],
                "level_lane_a": base["score"]["reading_level"],
                "level_final": final["score"]["reading_level"],
            }
            summary.append(info)
            (OUT / f"{wav.stem}.json").write_text(
                json.dumps({"summary": info, "rows": rows}, indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
            print(json.dumps(info, ensure_ascii=False))
            for r in rows:
                if r["changed"]:
                    print(
                        f"  {r['passage_word']!s:>12} | heard {r['spoken_word']!s:<12} | "
                        f"{r['lane_a_tag']} -> {r['final_tag']} | "
                        f"d_exp={r['d_exp']} d_hyp={r['d_hyp']} | "
                        f"exp /{r['expected_ipa']}/ heard /{r['heard_ipa']}/"
                    )

        overall = {
            "asr_load_s": round(asr_load_s, 1),
            "recogniser_load_s": round(rec_load_s, 1),
            "gpu_mb_after_load": round(loaded_mb),
            "gpu_mb_after_asr_model_only": round(asr_vram),
            "peak_gpu_mb": max((int(s["peak_gpu_mb"]) for s in summary), default=0),  # type: ignore[call-overload]
            "label_changes": dict(changes),
            "recordings": summary,
        }
        (OUT / "summary.json").write_text(
            json.dumps(overall, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in overall.items() if k != "recordings"}, indent=1))


class _NoVram:
    """Stand-in when there is no GPU."""

    baseline = 0
    peak_mb = 0.0

    def __enter__(self) -> "_NoVram":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def reset(self) -> None:
        return None

    def _used(self) -> int:
        return 0


if __name__ == "__main__":
    main()
