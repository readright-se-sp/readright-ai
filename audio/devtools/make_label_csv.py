"""Pre-filled labelling CSV for the 9 real recordings (data preparation only, no models).

Replays each cached run twice at the config.py defaults, Lane A alone (phonetic=False) and with
Lane B (phonetic=True), pairs the rows by position, cuts one listening clip per timed row from the
16 kHz WAV and writes devtools/out/labels/labels.csv (UTF-8 with BOM, so Excel shows the IPA).

    uv run python devtools/make_label_csv.py

Stops (exit 1) when the cache is missing or was built with other model ids. It never re-runs
cache_runs.py. Everything under devtools/out/ is git-ignored (student audio derivatives).
"""

import csv
import re
import shutil
import sys
import wave
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from replay import CACHE_DIR, Cached, Replayer, load_all, model_ids  # noqa: E402
from sweep_join import HELD_OUT_PREFIXES  # noqa: E402

from audio.config import AudioSettings  # noqa: E402

DATA = ROOT / "data"
LABELS = ROOT / "devtools" / "out" / "labels"
CLIPS = LABELS / "clips"
CLIP_PAD_MS = 300

COLUMNS = [
    "label",
    "needs_listen",
    "recording",
    "split",
    "row",
    "passage_index",
    "passage_word",
    "spoken_word",
    "start_ms",
    "end_ms",
    "clip",
    "lane_a_tag",
    "lane_b_tag",
    "check",
    "reason",
    "expected_phonemes",
    "heard_phonemes",
    "d_exp",
    "d_hyp",
    "notes",
]


@dataclass(frozen=True)
class Pair:
    row: int  # 1-based, order in the result
    lane_a: dict[str, Any]
    lane_b: dict[str, Any]


def pair_rows(
    lane_a: list[dict[str, Any]], lane_b: list[dict[str, Any]]
) -> tuple[list[Pair], list[str]]:
    """Pair the two runs by row order, checking that each pair covers the same passage positions.

    Returns the pairs and a list of problems (empty when the runs pair one-to-one). When the
    lengths differ, only the common prefix is paired.
    """
    problems: list[str] = []
    if len(lane_a) != len(lane_b):
        problems.append(f"row count differs: Lane A {len(lane_a)}, with Lane B {len(lane_b)}")
    pairs: list[Pair] = []
    for i, (a, b) in enumerate(zip(lane_a, lane_b, strict=False), start=1):
        if a["passage_indices"] != b["passage_indices"] or a["spoken_word"] != b["spoken_word"]:
            problems.append(
                f"row {i}: positions/words differ: {a['passage_indices']} {a['spoken_word']!r}"
                f" vs {b['passage_indices']} {b['spoken_word']!r}"
            )
        pairs.append(Pair(i, a, b))
    return pairs, problems


def clip_span_ms(
    start_s: float | None, end_s: float | None, duration_ms: int, pad_ms: int = CLIP_PAD_MS
) -> tuple[int, int] | None:
    """The row span plus or minus pad_ms, clamped to the file. None if untimed."""
    if start_s is None or end_s is None:
        return None
    start = max(0, round(start_s * 1000) - pad_ms)
    end = min(duration_ms, round(end_s * 1000) + pad_ms)
    return (start, end) if end > start else None


def clip_name(row: int, word: str | None) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", (word or "").lower()).strip("_") or "x"
    return f"{row:04d}_{safe}.wav"


def cut_clip(src: Path, dst: Path, span_ms: tuple[int, int]) -> None:
    with wave.open(str(src), "rb") as w:
        rate = w.getframerate()
        first = span_ms[0] * rate // 1000
        count = max(1, (span_ms[1] - span_ms[0]) * rate // 1000)
        w.setpos(min(first, w.getnframes()))
        frames = w.readframes(count)
        params = w.getparams()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        out.writeframes(frames)


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() * 1000 / w.getframerate())


def _fmt(x: float | None) -> str:
    return "" if x is None else f"{x:.3f}"


def build_record(c: Cached, pair: Pair, t_flag: float, clip_rel: str | None) -> dict[str, str]:
    a, b = pair.lane_a, pair.lane_b
    ph = b.get("phonetic") or {}
    span = a["start"] is not None and a["end"] is not None
    same = a["tag"] == b["tag"]
    d_exp = ph.get("d_exp")
    listen = (not same) or (ph.get("check") == "done" and d_exp is not None and d_exp > t_flag)
    return {
        "label": a["tag"] if same else "",
        "needs_listen": "yes" if listen else "",
        "recording": c.recording,
        "split": "heldout" if c.recording.startswith(HELD_OUT_PREFIXES) else "selection",
        "row": str(pair.row),
        "passage_index": " ".join(str(i) for i in a["passage_indices"]),
        "passage_word": a["passage_word"] or "",
        "spoken_word": a["spoken_word"] or "",
        "start_ms": str(round(a["start"] * 1000)) if span else "",
        "end_ms": str(round(a["end"] * 1000)) if span else "",
        "clip": clip_rel or "",
        "lane_a_tag": a["tag"],
        "lane_b_tag": b["tag"],
        "check": ph.get("check", ""),
        "reason": ph.get("reason", ""),
        "expected_phonemes": " ".join(ph.get("expected_phonemes", [])),
        "heard_phonemes": " ".join(ph.get("heard_phonemes", [])),
        "d_exp": _fmt(d_exp),
        "d_hyp": _fmt(ph.get("d_hyp")),
        "notes": "",
    }


def check_cache(caches: list[Cached], settings: AudioSettings) -> str | None:
    if not caches:
        return f"no cache in {CACHE_DIR}"
    want = model_ids(settings)
    stale = [c.recording for c in caches if c.models != want]
    if stale:
        return f"cache built with other model ids than config.py ({len(stale)} files): {stale}"
    missing = [c.recording for c in caches if not (DATA / f"{c.recording}.wav").exists()]
    if missing:
        return f"WAV missing in {DATA}: {missing}"
    return None


def main() -> None:
    settings = AudioSettings(_env_file=None)
    caches = load_all()
    if problem := check_cache(caches, settings):
        sys.exit(f"STOP: {problem}. Re-run devtools/cache_runs.py only with the user's approval.")

    replayer = Replayer(settings)
    if CLIPS.exists():
        shutil.rmtree(CLIPS)
    records: list[dict[str, str]] = []
    problems: list[str] = []
    for c in caches:
        lane_a = replayer.run(c, settings, phonetic=False)["words"]
        lane_b = replayer.run(c, settings, phonetic=True)["words"]
        pairs, issues = pair_rows(lane_a, lane_b)
        problems += [f"{c.recording}: {p}" for p in issues]
        wav = DATA / f"{c.recording}.wav"
        duration = wav_duration_ms(wav)
        for pair in pairs:
            span = clip_span_ms(pair.lane_a["start"], pair.lane_a["end"], duration)
            rel = None
            if span:
                word = pair.lane_a["passage_word"] or pair.lane_a["spoken_word"]
                rel = f"clips/{c.recording}/{clip_name(pair.row, word)}"
                cut_clip(wav, LABELS / rel, span)
            records.append(build_record(c, pair, settings.T_FLAG, rel))

    LABELS.mkdir(parents=True, exist_ok=True)
    with (LABELS / "labels.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(records)

    print(f"rows: {len(records)}")
    for rec, n in sorted(Counter(r["recording"] for r in records).items()):
        print(f"  {rec}: {n}")
    print(f"empty label: {sum(not r['label'] for r in records)}")
    print(f"needs_listen: {sum(bool(r['needs_listen']) for r in records)}")
    print(f"untimed (no clip): {sum(not r['clip'] for r in records)}")
    for (ta, tb), n in sorted(Counter((r["lane_a_tag"], r["lane_b_tag"]) for r in records).items()):
        print(f"  {ta} -> {tb}: {n}")
    print(f"pairing problems: {len(problems)}")
    for p in problems:
        print(f"  {p}")


if __name__ == "__main__":
    main()
