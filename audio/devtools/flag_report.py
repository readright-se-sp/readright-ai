"""Audio flags on the cached recordings, next to the user's yes/no labels. No models.

Replays each cached run (devtools/cache_runs.py) at the config.py defaults, computes the four
flags from the WAV in data/, and writes devtools/out/flags.md: per recording every metric, every
flag and the label. It also adds in-memory quiet copies of two recordings for is_hardly_audible
(-20 dB, -30 dB, and -20 dB with the original noise level mixed back in, which is what a
browser's automatic gain control produces).

    uv run python devtools/flag_report.py [--set NAME=VALUE ...]

``--set`` overrides a setting for this run only (for example ``--set MONO_MAX_ST=1.5``). Labels:
tests/fixtures/flags_labels.csv. Everything under devtools/out/ is git-ignored.
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from replay import Replayer, load_all, with_overrides  # noqa: E402

from audio.config import AudioSettings  # noqa: E402
from audio.flags import AudioFlags, compute_flags  # noqa: E402
from audio.recognition.audio_io import load_wav  # noqa: E402

DATA = ROOT / "data"
LABELS = ROOT / "tests" / "fixtures" / "flags_labels.csv"
OUT = ROOT / "devtools" / "out" / "flags.md"
FLAGS = ("word_by_word", "disregard_punctuation", "monotonous", "hardly_audible")
SYNTHETIC_FROM = ("mamawas", "ringringra")  # recording-name prefixes
ATTENUATE_DB = (20, 30)


def read_labels(path: Path = LABELS) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["recording"]: row for row in csv.DictReader(f)}


def fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return "" if value is None else str(value)


def cell(flag: bool, label: str | None) -> str:
    mine = "yes" if flag else "no"
    if label is None:
        return mine
    return mine if mine == label else f"**{mine}** (label {label})"


def gain(wav: np.ndarray[Any, Any], db: float) -> np.ndarray[Any, Any]:
    return (wav * 10 ** (-db / 20)).astype(np.float32)


def with_noise(
    wav: np.ndarray[Any, Any], db: float, noise_dbfs: float | None
) -> np.ndarray[Any, Any]:
    """The quiet copy plus white noise at the original noise level."""
    quiet = gain(wav, db).astype(np.float64)
    level = -60.0 if noise_dbfs is None else noise_dbfs
    noise = np.random.default_rng(0).normal(0, 10 ** (level / 20), quiet.size)
    return np.clip(quiet + noise, -1, 1).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()
    overrides: dict[str, Any] = {}
    base = AudioSettings(_env_file=None)
    for item in args.set:
        name, _, raw = item.partition("=")
        overrides[name] = type(getattr(base, name))(raw)
    settings = with_overrides(base, **overrides)

    caches = load_all()
    if not caches:
        sys.exit("STOP: no cache in devtools/out/cache. Re-run cache_runs.py only with approval.")
    labels = read_labels() if LABELS.exists() else {}
    replayer = Replayer(settings)

    lines = ["# Audio flags on the cached recordings", ""]
    if overrides:
        lines += [f"Settings overridden for this run: `{overrides}`", ""]
    metrics_rows: list[tuple[str, AudioFlags]] = []
    synthetic: list[tuple[str, AudioFlags]] = []
    flag_rows: list[str] = []
    disagreements = 0
    for c in caches:
        wav = load_wav(DATA / f"{c.recording}.wav")
        rows = replayer.run(c, settings, phonetic=True)["words"]
        flags = compute_flags(wav, rows, c.passage, settings)
        metrics_rows.append((c.recording, flags))
        label = labels.get(c.recording, {})
        cells = [cell(getattr(flags, f), label.get(f) or None) for f in FLAGS]
        disagreements += sum("label" in x for x in cells)
        flag_rows.append(
            f"| {c.recording} | " + " | ".join(cells) + f" | {label.get('notes', '')} |"
        )
        if c.recording.startswith(SYNTHETIC_FROM):
            noise = flags.metrics.get("audible_noise_dbfs")
            noise_db = float(noise) if isinstance(noise, float) else None
            for db in ATTENUATE_DB:
                variant = compute_flags(gain(wav, db), rows, c.passage, settings)
                synthetic.append((f"{c.recording} -{db} dB", variant))
            variant = compute_flags(with_noise(wav, 20, noise_db), rows, c.passage, settings)
            synthetic.append((f"{c.recording} -20 dB + original noise", variant))

    lines += ["## Flags (bold = disagrees with the label)", ""]
    lines += [
        "| recording | word_by_word | disregard_punctuation | monotonous | hardly_audible | notes |"
    ]
    lines += ["|---|---|---|---|---|---|", *flag_rows, ""]
    lines += [f"Disagreements with the labels: {disagreements}", ""]

    keys = sorted({k for _, f in metrics_rows for k in f.metrics})
    lines += ["## Metrics", "", "| recording | " + " | ".join(keys) + " |"]
    lines += ["|---" * (len(keys) + 1) + "|"]
    for name, f in metrics_rows:
        lines.append(f"| {name} | " + " | ".join(fmt(f.metrics.get(k)) for k in keys) + " |")
    lines += ["", "## Quiet copies (hardly_audible)", ""]
    lines += [
        "| copy | hardly_audible | speech dBFS | noise dBFS | SNR dB |",
        "|---|---|---|---|---|",
    ]
    for name, f in synthetic:
        m = f.metrics
        lines.append(
            f"| {name} | {'yes' if f.hardly_audible else 'no'}"
            f" | {fmt(m.get('audible_speech_dbfs'))} | {fmt(m.get('audible_noise_dbfs'))}"
            f" | {fmt(m.get('audible_snr_db'))} |"
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
