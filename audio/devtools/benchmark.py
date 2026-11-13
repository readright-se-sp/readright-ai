"""Timing benchmark of the real AudioPipeline.run (CrisperWhisper + Lane A + Lane B) on audio/data/.

Models load once through audio.wiring, one warm-up run is not counted, then every WAV runs
--runs times (default 3) with PHONETIC_ENABLED=true. Stages are timed by wrapping the pipeline's
own instances from here (no change to src/); torch.cuda.synchronize() runs before each end time.

Stages: 1 WAV read, 2 ASR (transcribe + word alignment), 3 Lane A rows, 4 Lane B recogniser,
5 Lane B join + compare + arbitrate (PhoneticReviewer.review minus stage 4: also includes passage
prep, row adapting and write-back), 6 post-pass + scoring, 7 total. Lane A only = total - 4 - 5.

Writes devtools/out/benchmark.md (git-ignored). Dev machine numbers: they exclude the server's
download, WAV conversion, clip cutting and S3 upload.

    uv run python devtools/benchmark.py [--runs 3] [--readme]
"""

import argparse
import datetime
import gc
import importlib.metadata as md
import statistics
import subprocess
import sys
import time
import wave
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

import torch  # noqa: E402
import whisperx  # type: ignore[import-untyped]  # noqa: E402
from recordings import passage_for  # noqa: E402
from run_pipeline import VramSampler  # noqa: E402

from audio.config import AudioSettings  # noqa: E402
from audio.wiring import (  # noqa: E402
    build_audio_pipeline,
    build_phonetic_reviewer,
    load_phoneme_recognizer,
    load_transcriber,
)

DATA = ROOT / "data"
OUT = ROOT / "devtools" / "out" / "benchmark.md"
README = ROOT / "README.md"
SECTION = "## Benchmark (dev machine, not deployment)"

STAGES = [
    ("wav_read", "1 WAV read"),
    ("asr", "2 ASR (transcribe + align)"),
    ("lane_a", "3 Lane A rows"),
    ("recogniser", "4 Lane B recogniser"),
    ("lane_b_rest", "5 Lane B join + compare + arbitrate"),
    ("post_pass", "6 post-pass + scoring"),
    ("total", "7 total"),
    ("lane_a_only", "Lane A only (7 - 4 - 5)"),
]


class Timer:
    """Accumulates synchronised wall time per stage within one run."""

    def __init__(self) -> None:
        self.acc: dict[str, float] = defaultdict(float)
        self.errors: list[BaseException] = []

    def wrap(
        self, name: str, fn: Callable[..., Any], *, record_errors: bool = False
    ) -> Callable[..., Any]:
        def inner(*args: Any, **kwargs: Any) -> Any:
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                if record_errors:
                    self.errors.append(exc)
                raise
            finally:
                torch.cuda.synchronize()
                self.acc[name] += (time.perf_counter() - t0) * 1000

        return inner


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def nvidia_smi() -> str:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return "n/a"


def version(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return "n/a"


def is_oom(exc: BaseException) -> bool:
    return isinstance(exc, torch.cuda.OutOfMemoryError) or "out of memory" in str(exc).lower()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--readme", action="store_true", help="also write the summary into audio/README.md"
    )
    args = parser.parse_args()

    wavs = sorted(DATA.glob("*.wav"))
    if not wavs:
        sys.exit(f"No WAV files in {DATA}")
    if not torch.cuda.is_available():
        sys.exit("CUDA not available; this benchmark does not fall back to CPU")

    settings = AudioSettings(PHONETIC_ENABLED=True)
    timer = Timer()
    try:
        with VramSampler() as vram:
            torch.cuda.reset_peak_memory_stats()
            t0 = time.perf_counter()
            transcriber = load_transcriber(settings)
            torch.cuda.synchronize()
            asr_load_ms = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            recogniser = load_phoneme_recognizer(settings)
            torch.cuda.synchronize()
            rec_load_ms = (time.perf_counter() - t0) * 1000
            assert recogniser is not None
            reviewer = build_phonetic_reviewer(recogniser, settings)
            pipeline = build_audio_pipeline(transcriber, reviewer)
            loaded_mb = vram._used() / 2**20  # noqa: SLF001

            # Instance-level wrappers (devtool only; src/ is untouched).
            whisperx.load_audio = timer.wrap("wav_read", whisperx.load_audio)  # type: ignore[attr-defined]
            whisperx.align = timer.wrap("asr", whisperx.align)  # type: ignore[attr-defined]
            transcriber._model.transcribe = timer.wrap("asr", transcriber._model.transcribe)  # noqa: SLF001
            pipeline._lane_a.rows = timer.wrap("lane_a", pipeline._lane_a.rows)  # noqa: SLF001
            recogniser.recognise = timer.wrap(
                "recogniser", recogniser.recognise, record_errors=True
            )  # type: ignore[method-assign]
            reviewer.review = timer.wrap("reviewer", reviewer.review)  # type: ignore[method-assign]
            pipeline._post_pass.run = timer.wrap("post_pass", pipeline._post_pass.run)  # noqa: SLF001
            pipeline._scorer.score = timer.wrap("post_pass", pipeline._scorer.score)  # noqa: SLF001

            def run_once(wav: Path) -> dict[str, float]:
                timer.acc.clear()
                timer.errors.clear()
                # Release cached allocator blocks between runs (6 GiB card);
                # outside the timed region.
                gc.collect()
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                t = time.perf_counter()
                result = pipeline.run(str(wav), passage_for(wav))
                torch.cuda.synchronize()
                total = (time.perf_counter() - t) * 1000
                if (
                    timer.errors
                ):  # the reviewer swallows Lane B failures; do not benchmark a skipped lane
                    exc = timer.errors[0]
                    if is_oom(exc):
                        raise exc
                    raise RuntimeError(f"Lane B failed (phonetic_check=skipped): {exc!r}")
                if not any(r.get("phonetic") for r in result["words"]):
                    raise RuntimeError(
                        "Lane B produced no phonetic evidence; the run is not comparable"
                    )
                a = timer.acc
                rec = a["recogniser"]
                return {
                    "wav_read": a["wav_read"],
                    "asr": a["asr"],
                    "lane_a": a["lane_a"],
                    "recogniser": rec,
                    "lane_b_rest": a["reviewer"] - rec,
                    "post_pass": a["post_pass"],
                    "total": total,
                    "lane_a_only": total - rec - (a["reviewer"] - rec),
                }

            print(
                f"loaded: ASR {asr_load_ms:.0f} ms, recogniser {rec_load_ms:.0f} ms; warming up",
                flush=True,
            )
            run_once(wavs[0])  # warm-up, not counted
            torch.cuda.reset_peak_memory_stats()
            vram.reset()

            runs: list[dict[str, float]] = []
            durations: list[float] = []
            per_file: list[tuple[str, float, float]] = []
            for wav in wavs:
                dur = duration_s(wav)
                totals = []
                for i in range(args.runs):
                    r = run_once(wav)
                    runs.append(r)
                    durations.append(dur)
                    totals.append(r["total"])
                    print(
                        f"{wav.name[:12]} run {i + 1}: total {r['total']:.0f} ms "
                        f"({dur:.1f}s audio)",
                        flush=True,
                    )
                per_file.append((wav.name, dur, statistics.median(totals)))
            torch_peak_mb = torch.cuda.max_memory_allocated() / 2**20
            torch_reserved_mb = torch.cuda.max_memory_reserved() / 2**20
            device_peak_mb = vram.peak_mb
            smi_end = nvidia_smi()
    except BaseException as exc:  # noqa: BLE001
        if is_oom(exc):
            sys.exit(f"STOPPED: CUDA out of memory ({type(exc).__name__}: {exc}). No CPU fallback.")
        raise

    props = torch.cuda.get_device_properties(0)
    ms_per_s = [r["total"] / d for r, d in zip(runs, durations, strict=True)]
    lane_a_ms_per_s = [r["lane_a_only"] / d for r, d in zip(runs, durations, strict=True)]
    today = datetime.date.today().isoformat()

    rows = ["| Stage | median ms | p95 ms | max ms |", "|---|---:|---:|---:|"]
    for key, label in STAGES:
        v = [r[key] for r in runs]
        rows.append(f"| {label} | {statistics.median(v):.0f} | {pct(v, 0.95):.0f} | {max(v):.0f} |")
    table = "\n".join(rows)
    extra = (
        f"- Total per second of audio: median {statistics.median(ms_per_s):.0f} ms/s, "
        f"p95 {pct(ms_per_s, 0.95):.0f}, max {max(ms_per_s):.0f} "
        f"(Lane A only: median {statistics.median(lane_a_ms_per_s):.0f} ms/s)\n"
        f"- Peak VRAM: torch max_memory_allocated {torch_peak_mb:.0f} MB "
        f"(reserved {torch_reserved_mb:.0f} MB); whole-device peak in use {device_peak_mb:.0f} MB "
        f"(torch + CTranslate2); {loaded_mb:.0f} MB in use after load\n"
        f"- Model load: CrisperWhisper 2.0 large + alignment {asr_load_ms:.0f} ms; "
        f"Lane B recogniser {rec_load_ms:.0f} ms\n"
        f"- {len(wavs)} recordings x {args.runs} runs = {len(runs)} timed runs, "
        "one warm-up not counted"
    )
    machine = (
        f"- GPU: {props.name}, {props.total_memory / 2**30:.1f} GiB\n"
        f"- nvidia-smi after run: {smi_end} (name, total, used, driver)\n"
        f"- CUDA (torch build): {torch.version.cuda}; torch {torch.__version__}; "
        f"ctranslate2 {version('ctranslate2')}; whisperx {version('whisperx')}\n"
        f"- ASR_COMPUTE_TYPE={settings.ASR_COMPUTE_TYPE}, "
        f"ASR_BATCH_SIZE={settings.ASR_BATCH_SIZE}, "
        f"PHONETIC_ENABLED=true, PHONEME_MODEL={settings.PHONEME_MODEL} "
        f"(fp16={settings.PHONEME_FP16})"
    )
    files = "\n".join(f"| {n[:14]}... | {d:.1f} | {m:.0f} |" for n, d, m in per_file)
    note = (
        "Dev machine, not deployment. Excludes the server's download, WAV conversion, clip cutting "
        "and S3 upload."
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        f"# Audio pipeline benchmark ({today})\n\n{note}\n\n## Summary\n\n{table}\n\n{extra}\n\n"
        f"## Machine\n\n{machine}\n\n## Per recording (median total)\n\n"
        f"| Recording | audio s | median total ms |\n|---|---:|---:|\n{files}\n",
        encoding="utf-8",
    )
    print(f"\n{table}\n\n{extra}\n\n{machine}\n\nwrote {OUT}")

    if args.readme:
        section = (
            f"{SECTION}\n\nMeasured {today} with `devtools/benchmark.py` "
            "(real `AudioPipeline.run`, "
            f"Lane B on, {len(wavs)} recordings x {args.runs} runs after one warm-up). {note}\n"
            f"Stage 5 is `PhoneticReviewer.review` minus stage 4 (it also covers passage prep, row "
            f"adapting and write-back).\n\n{table}\n\n{extra}\n\nMachine:\n{machine}\n"
        )
        text = README.read_text(encoding="utf-8")
        if SECTION in text:
            head, _, tail = text.partition(SECTION)
            nxt = tail.find("\n## ")
            text = head + section + (tail[nxt:] if nxt != -1 else "")
        else:
            text = text.rstrip("\n") + "\n\n" + section
        README.write_text(text, encoding="utf-8")
        print(f"updated {README}")


if __name__ == "__main__":
    main()
