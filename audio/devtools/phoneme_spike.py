"""B2 spike: pick the phoneme model and precision from measured numbers.

Run from ``audio/``:  ``uv run python devtools/phoneme_spike.py``

1. Whisper (``openai/whisper-small.en``) transcribes every WAV in ``data/``; transcripts are cached
   in ``devtools/out/whisper/``. The reference is the Whisper words phonemised with ``build_g2p``
   (first variant), so PER measures how well a recogniser follows what was said, not correctness.
2. Each candidate x {fp32, fp16} runs on CUDA; the chosen model then runs fp32 on CPU in a child
   process (so its peak RSS is clean).
3. Raw rows go to ``devtools/out/results.csv`` (git-ignored); the summary tables go to
   ``plans/phase-B2-spike.md`` (aggregate numbers only).
"""

import argparse
import csv
import gc
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from recordings import PASSAGE_NAME_BY_PREFIX as PASSAGE_NAMES  # noqa: E402
from recordings import passage_for  # noqa: E402

from audio.passage import PassagePreparer, spoken_form  # noqa: E402
from audio.phonetics.g2p import G2PError, build_g2p  # noqa: E402
from audio.phonetics.ipa import INVENTORY  # noqa: E402
from audio.recognition.audio_io import SAMPLE_RATE, load_wav  # noqa: E402
from audio.recognition.phoneme_recognizer import Wav2Vec2PhonemeRecognizer  # noqa: E402
from audio.recognition.vocab import unmapped_tokens  # noqa: E402
from audio.settings import AudioSettings  # noqa: E402

OUT = ROOT / "devtools" / "out"
SUMMARY = ROOT / "plans" / "phase-B2-spike.md"
CANDIDATES = [
    "facebook/wav2vec2-lv-60-espeak-cv-ft",
    "facebook/wav2vec2-xlsr-53-espeak-cv-ft",
]
WHISPER = "openai/whisper-small.en"
WINDOW_PAD_MS = 80
FIELDS = [
    "config", "recording", "audio_s", "per", "coverage", "warm_s", "rtf", "peak_mem_mb",
    "nan_inf", "n_phonemes", "n_ref", "outside_inventory",
]  # fmt: skip


def label(model: str, device: str, fp16: bool) -> str:
    return f"{model.split('/')[-1]} / {device} / {'fp16' if fp16 else 'fp32'}"


def levenshtein(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def recordings() -> list[Path]:
    return sorted((ROOT / "data").glob("*.wav"))


# --- reference ---------------------------------------------------------------------------


def transcribe_all(wavs: list[Path]) -> None:
    cache = OUT / "whisper"
    cache.mkdir(parents=True, exist_ok=True)
    todo = [w for w in wavs if not (cache / f"{w.stem}.json").exists()]
    if not todo:
        return
    import torch
    from transformers import pipeline

    device = 0 if torch.cuda.is_available() else -1
    asr = pipeline("automatic-speech-recognition", model=WHISPER, device=device)
    for wav in todo:
        samples = load_wav(wav)
        result = asr(
            {"raw": samples, "sampling_rate": SAMPLE_RATE},
            chunk_length_s=30,
            return_timestamps="word",
        )
        words = [
            {"text": c["text"].strip(), "start": c["timestamp"][0], "end": c["timestamp"][1]}
            for c in result["chunks"]
        ]
        (cache / f"{wav.stem}.json").write_text(json.dumps(words), encoding="utf-8")
        print(f"whisper: {wav.name} -> {len(words)} words", flush=True)
    del asr
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_reference(wav: Path, g2p, duration_s: float):
    """Return (phoneme list, word windows [(start_ms, end_ms, [phonemes])])."""
    words = json.loads((OUT / "whisper" / f"{wav.stem}.json").read_text(encoding="utf-8"))
    ref: list[str] = []
    windows = []
    for w in words:
        form = spoken_form(w["text"])
        if not form:
            continue
        try:
            seq = g2p.phonemize([form])[0]
        except G2PError:
            continue
        start = int((w["start"] or 0) * 1000)
        end = int((w["end"] if w["end"] is not None else duration_s) * 1000)
        windows.append((start, end, list(seq), form))
        ref.extend(seq)
    return ref, windows


def passage_reference(wav: Path, g2p):
    """Reference from the mapped passage text: first variant of each token, in order."""
    prepared = PassagePreparer(g2p).prepare(passage_for(wav))
    ref: list[str] = []
    for token in prepared.tokens:
        ref.extend(prepared.pronunciations[token.key][0])
    return ref


# --- measurement -------------------------------------------------------------------------


def peak_rss_mb() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return c.PeakWorkingSetSize / 2**20
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def run_config(model: str, device: str, fp16: bool, wavs: list[Path], g2p) -> list[dict]:
    import torch

    cfg = label(model, device, fp16)
    print(f"== {cfg}", flush=True)
    settings = AudioSettings(_env_file=None)
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    rec = Wav2Vec2PhonemeRecognizer(
        model, device, fp16, settings.phoneme_chunk_s, settings.phoneme_context_s
    )
    outside_ids = unmapped_tokens(rec.token_map)
    print(f"   vocab ids with non-English segments: {len(outside_ids)}", flush=True)
    rows = []
    for wav in wavs:
        samples = load_wav(wav)
        audio_s = len(samples) / SAMPLE_RATE
        rec.recognise(wav, None)  # warm-up
        times = []
        for _ in range(2):
            if device == "cuda":
                torch.cuda.synchronize()
            t0 = time.perf_counter()
            tokens = rec.recognise(wav, None)
            if device == "cuda":
                torch.cuda.synchronize()
            times.append(time.perf_counter() - t0)
        warm = statistics.median(times)

        ref, windows = load_reference(wav, g2p, audio_s)
        hyp = [t.symbol for t in tokens]
        per = levenshtein(hyp, ref) / max(1, len(ref))
        inside = 0
        for t in tokens:
            mid = (t.start_ms + t.end_ms) / 2
            if any(s - WINDOW_PAD_MS <= mid <= e + WINDOW_PAD_MS for s, e, _, _ in windows):
                inside += 1
        nan_inf = 0
        if fp16:
            nan_inf = int((~np.isfinite(rec.logprobs(samples))).sum())
        peak = torch.cuda.max_memory_allocated() / 2**20 if device == "cuda" else peak_rss_mb()
        outside = {}
        for s in hyp:
            if s not in INVENTORY:
                outside[s] = outside.get(s, 0) + 1
        rows.append(
            {
                "config": cfg,
                "recording": wav.stem,
                "audio_s": round(audio_s, 1),
                "per": round(per, 4),
                "coverage": round(inside / max(1, len(tokens)), 4),
                "warm_s": round(warm, 3),
                "rtf": round(warm / audio_s, 4),
                "peak_mem_mb": round(peak),
                "nan_inf": nan_inf,
                "n_phonemes": len(hyp),
                "n_ref": len(ref),
                "outside_inventory": json.dumps(outside, ensure_ascii=False),
            }
        )
        print(f"   {wav.stem[:12]} per={per:.3f} warm={warm:.2f}s", flush=True)
        if wav.name.startswith("everysatur") and wav == next(
            w for w in wavs if w.name.startswith("everysatur")
        ):
            dump_aligned(cfg, tokens, windows)
    del rec
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    return rows


def dump_aligned(cfg: str, tokens, windows) -> None:
    lines = [f"# {cfg}  (reference word | recogniser phonemes whose midpoint is in the window)"]
    for start, end, ref_seq, form in windows:
        heard = [
            f"{t.symbol}@{t.start_ms}"
            for t in tokens
            if start - WINDOW_PAD_MS <= (t.start_ms + t.end_ms) / 2 <= end + WINDOW_PAD_MS
        ]
        lines.append(
            f"{form:<14} [{start}-{end} ms]  ref: {' '.join(ref_seq):<20} heard: {' '.join(heard)}"
        )
    name = cfg.replace("|", "_").replace("/", "_")
    (OUT / f"aligned_{name}.txt").write_text("\n".join(lines), encoding="utf-8")


def append_rows(rows: list[dict]) -> None:
    path = OUT / "results.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


# --- summary -----------------------------------------------------------------------------


def read_rows() -> dict[str, list[dict]]:
    by_cfg: dict[str, list[dict]] = {}
    with (OUT / "results.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_cfg.setdefault(row["config"], []).append(row)
    return by_cfg


def med(rows: list[dict], key: str) -> float:
    return statistics.median(float(r[key]) for r in rows)


def summarise() -> dict:
    by_cfg = read_rows()
    stats = {}
    for cfg, rows in by_cfg.items():
        outside: dict[str, int] = {}
        for r in rows:
            for s, n in json.loads(r["outside_inventory"]).items():
                outside[s] = outside.get(s, 0) + n
        longest = max(rows, key=lambda r: float(r["audio_s"]))
        stats[cfg] = {
            "rows": rows,
            "per": med(rows, "per"),
            "coverage": med(rows, "coverage"),
            "warm": med(rows, "warm_s"),
            "rtf": med(rows, "rtf"),
            "peak": max(float(r["peak_mem_mb"]) for r in rows),
            "nan_inf": sum(int(r["nan_inf"]) for r in rows),
            "outside": outside,
            "longest_audio": float(longest["audio_s"]),
            "longest_warm": float(longest["warm_s"]),
        }
    return stats


def decide(stats: dict) -> dict:
    gpu32 = {
        m: stats[label(m, "cuda", False)] for m in CANDIDATES if label(m, "cuda", False) in stats
    }
    stop = []
    if all(s["per"] > 0.6 for s in gpu32.values()):
        stop.append("median PER is above 0.6 for both models")
    for cfg, s in stats.items():
        if " / cuda / " in cfg and s["longest_warm"] > 15 * s["longest_audio"] / 67:
            stop.append(
                f"{cfg}: the {s['longest_audio']} s recording took {s['longest_warm']} s warm"
            )
    a, b = (gpu32[m] for m in CANDIDATES)
    if abs(a["per"] - b["per"]) <= 0.02:
        winner = CANDIDATES[0] if a["warm"] <= b["warm"] else CANDIDATES[1]
        why = "medians within 0.02, so the lower warm GPU latency wins"
    else:
        winner = CANDIDATES[0] if a["per"] < b["per"] else CANDIDATES[1]
        why = "lowest median PER"
    f32 = stats[label(winner, "cuda", False)]
    f16 = stats[label(winner, "cuda", True)]
    use_fp16 = f16["nan_inf"] == 0 and (f16["per"] - f32["per"]) <= 0.01
    return {"stop": stop, "model": winner, "why": why, "fp16": use_fp16}


def write_summary(stats: dict, decision: dict) -> None:
    lines = [
        "# Phase B2 spike: phoneme model and precision",
        "",
        "Aggregate numbers only (no transcripts or student content). Raw per-recording rows are in",
        "`devtools/out/results.csv` (git-ignored). Run on the dev GPU (GTX 1660 Ti, 6 GB); A10G",
        "numbers come from the deployment benchmark.",
        "",
        f"Reference: `{WHISPER}` word transcript, phonemised with `build_g2p` (first variant). PER",
        "measures how well each recogniser follows what was said, not whether the reading was",
        f"correct. Recordings: {len(next(iter(stats.values()))['rows'])} WAVs from `audio/data/`.",
        "",
        "## Results (medians over recordings)",
        "",
        "| Config | PER | Word-window coverage | Warm s | RTF | Peak mem (MB) | NaN/Inf |",
        "|---|---|---|---|---|---|---|",
    ]
    for cfg, s in stats.items():
        mem = f"{s['peak']:.0f}"
        lines.append(
            f"| `{cfg}` | {s['per']:.3f} | {s['coverage']:.3f} | {s['warm']:.2f} | {s['rtf']:.3f} "
            f"| {mem} | {s['nan_inf'] if '/ fp16' in cfg else '-'} |"
        )
    lines += [
        "",
        "Peak memory is `torch.cuda.max_memory_allocated` on CUDA and process peak RSS on CPU.",
        "",
        "## Longest recording",
        "",
        "| Config | Audio s | Warm s |",
        "|---|---|---|",
    ]
    for cfg, s in stats.items():
        lines.append(f"| `{cfg}` | {s['longest_audio']} | {s['longest_warm']} |")
    lines += ["", "## Phonemes outside the English inventory (counts over all recordings)", ""]
    for cfg, s in stats.items():
        if "/ fp32" in cfg:
            items = ", ".join(
                f"`{k}` x{v}" for k, v in sorted(s["outside"].items(), key=lambda kv: -kv[1])
            )
            lines.append(f"- `{cfg}`: {items or 'none'}")
    lines += ["", "## Decision", ""]
    if decision["stop"]:
        lines += ["**Stop conditions hit:**", *[f"- {x}" for x in decision["stop"]]]
    else:
        lines += [
            f"- Model: `{decision['model']}` ({decision['why']}).",
            f"- Precision: `AUDIO_PHONEME_FP16={'true' if decision['fp16'] else 'false'}`.",
        ]
    lines += [
        "",
        "## Notes",
        "",
        "- The two models are within noise of each other: PER differs by about 0.01 and warm",
        "  GPU latency by about 1%. The pre-agreed rule (medians within 0.02, so the lower",
        "  latency wins) picks the xlsr model; lv-60 has the slightly lower PER and fewer",
        "  non-English phones.",
        "- fp16 produced NaN/Inf logits on the GTX 1660 Ti (no tensor cores) for every",
        "  recording, so its PER is 1.0 and it is unusable here. This says nothing about the",
        "  A10G; re-check there in the deployment benchmark before enabling it.",
        "- Word-window coverage is near 1.0 because Whisper word windows plus 80 ms padding",
        "  are almost contiguous over the speech; it is an early read on B3's join, not a",
        "  sharp measure.",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def passage_report(g2p) -> None:
    """B1.1: PER of the chosen model against the passage text, next to the Whisper reference."""
    import torch

    wavs = recordings()
    transcribe_all(wavs)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    settings = AudioSettings(_env_file=None)
    rec = Wav2Vec2PhonemeRecognizer(
        CANDIDATES[1], device, False, settings.phoneme_chunk_s, settings.phoneme_context_s
    )
    rows = []
    for wav in wavs:
        samples = load_wav(wav)
        hyp = [t.symbol for t in rec.recognise(wav, None)]
        ref_whisper, _ = load_reference(wav, g2p, len(samples) / SAMPLE_RATE)
        ref_passage = passage_reference(wav, g2p)
        rows.append(
            {
                "recording": wav.stem,
                "passage": next(v for k, v in PASSAGE_NAMES.items() if wav.name.startswith(k)),
                "per_passage": levenshtein(hyp, ref_passage) / max(1, len(ref_passage)),
                "per_whisper": levenshtein(hyp, ref_whisper) / max(1, len(ref_whisper)),
            }
        )
        print(f"   {wav.stem[:12]} passage={rows[-1]['per_passage']:.3f}", flush=True)
    lines = [
        "",
        "## Passage reference (B1.1)",
        "",
        f"`{CANDIDATES[1]}`, fp32, {device}. Reference: G2P of the mapped passage text, first",
        "variant (`--reference passage`). The Whisper column is the B2 reference on the same",
        "recordings. The B2 model choice is unchanged.",
        "",
        "| Passage | Recordings | Median PER (passage ref) | Median PER (Whisper ref) |",
        "|---|---|---|---|",
    ]
    for name in dict.fromkeys(r["passage"] for r in rows):
        group = [r for r in rows if r["passage"] == name]
        lines.append(
            f"| `{name}` | {len(group)} | {statistics.median(r['per_passage'] for r in group):.3f} "
            f"| {statistics.median(r['per_whisper'] for r in group):.3f} |"
        )
    lines += [
        "",
        "Five highest PER against the passage:",
        "",
        "| Recording | Passage | PER (passage ref) | PER (Whisper ref) |",
        "|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: -r["per_passage"])[:5]:
        lines.append(
            f"| `{r['recording'][:14]}` | `{r['passage']}` | {r['per_passage']:.3f} "
            f"| {r['per_whisper']:.3f} |"
        )
    text = SUMMARY.read_text(encoding="utf-8")
    head = text.split("\n## Passage reference (B1.1)")[0].rstrip("\n")
    SUMMARY.write_text(head + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-only", metavar="MODEL", help="child mode: fp32 on CPU for MODEL")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument(
        "--reference",
        choices=["passage"],
        help="B1.1: re-run the chosen model against the passage text and append the table",
    )
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    wavs = recordings()

    if args.summary_only:
        stats = summarise()
        decision = decide(stats)
        write_summary(stats, decision)
        print(json.dumps(decision, indent=2))
        return

    settings = AudioSettings(_env_file=None)
    g2p = build_g2p(settings)
    if args.reference == "passage":
        passage_report(g2p)
        return
    if args.cpu_only:
        append_rows(run_config(args.cpu_only, "cpu", False, wavs, g2p))
        return

    print(f"{len(wavs)} recordings", flush=True)
    transcribe_all(wavs)
    (OUT / "results.csv").unlink(missing_ok=True)
    for model in CANDIDATES:
        for fp16 in (False, True):
            append_rows(run_config(model, "cuda", fp16, wavs, g2p))
    stats = summarise()
    decision = decide(stats)
    if decision["stop"]:
        write_summary(stats, decision)
        print("STOP:", decision["stop"])
        return
    subprocess.run([sys.executable, __file__, "--cpu-only", decision["model"]], check=True)
    stats = summarise()
    write_summary(stats, decision)
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
