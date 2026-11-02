"""End-to-end Lane B smoke-test on the real recordings in ``audio/data/``.

Run from ``audio/``:
    uv run python devtools/run_lane_b.py
    # or test a single recording:
    uv run python devtools/run_lane_b.py --wav ringringraf85a6a1647144290b7c62c738e3e105a.wav

Outputs
-------
- Formatted console table matching the evaluation screenshot:
  [#] [passage] [transcribed] [verdict] [miscue] [start] [end] [d_exp] [phonetics]
- Markdown report: ``devtools/out/lane_b_results.md``
- Rich interactive HTML report: ``devtools/out/lane_b_report.html``
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from recordings import passage_for  # noqa: E402

from audio.alignment.text import normalize  # noqa: E402
from audio.arbitration.arbiter import arbitrate  # noqa: E402
from audio.config import AudioSettings  # noqa: E402
from audio.lane_b.compare import hypothesis_variants  # noqa: E402
from audio.lane_b.join import join  # noqa: E402
from audio.passage import PassagePreparer, spoken_form  # noqa: E402
from audio.phonetics.g2p import build_g2p  # noqa: E402
from audio.recognition.phoneme_recognizer import Wav2Vec2PhonemeRecognizer  # noqa: E402
from audio.types import (  # noqa: E402
    AlignOp,
    AsrWord,
    LaneAResult,
    PreparedPassage,
    TraceStep,
    Unit,
    WordLabel,
)

logger = logging.getLogger(__name__)

OUT = ROOT / "devtools" / "out"
WHISPER_CACHE = OUT / "whisper"
CRISPER_CACHE = OUT / "crisperwhisper"


# ---------------------------------------------------------------------------
# LaneAResult synthesis from Whisper transcripts
# ---------------------------------------------------------------------------


def _load_asr_words(wav: Path, asr_engine: str = "crisperwhisper") -> tuple[list[dict], str]:
    """Load cached word-level transcripts from CrisperWhisper or Whisper."""
    if asr_engine == "crisperwhisper":
        p = CRISPER_CACHE / f"{wav.stem}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")), "crisperwhisper_2.0_large"
    p = WHISPER_CACHE / f"{wav.stem}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8")), "whisper_small"
    return [], "none"


def _key(text: str) -> str:
    """Lane A's normalised form of an ASR word (several words joined)."""
    return "".join(normalize(text))


def _build_lane_a(passage: PreparedPassage, whisper_words: list[dict]) -> LaneAResult:
    """Synthesise a LaneAResult from Whisper words aligned to passage tokens.

    Strategy: simple greedy left-to-right alignment on spoken_form / _key.
    Words that match a passage token get CORRECT; extra words become INSERTION;
    skipped tokens become OMISSION. All timings come from Whisper.
    """
    passage_tokens = list(passage.tokens)

    # --- align Whisper words to passage tokens ---
    asr_forms = [spoken_form(w["text"]) for w in whisper_words]
    passage_keys = [t.key for t in passage_tokens]

    # dp: O(n*m) needleman-wunsch, match only when keys are equal
    n_asr, n_pass = len(asr_forms), len(passage_keys)
    score = [[0] * (n_pass + 1) for _ in range(n_asr + 1)]
    for i in range(n_asr + 1):
        score[i][0] = -i
    for j in range(n_pass + 1):
        score[0][j] = -j
    for i in range(1, n_asr + 1):
        for j in range(1, n_pass + 1):
            match = (
                1
                if (
                    asr_forms[i - 1] == passage_keys[j - 1]
                    or _key(whisper_words[i - 1]["text"]) == passage_keys[j - 1]
                )
                else -1
            )
            score[i][j] = max(score[i - 1][j - 1] + match, score[i - 1][j] - 1, score[i][j - 1] - 1)

    # backtrace
    ops: list[tuple[str, int | None, int | None]] = []  # (op, asr_idx, pass_idx)
    i, j = n_asr, n_pass
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            match = (
                1
                if (
                    asr_forms[i - 1] == passage_keys[j - 1]
                    or _key(whisper_words[i - 1]["text"]) == passage_keys[j - 1]
                )
                else -1
            )
            if score[i][j] == score[i - 1][j - 1] + match:
                ops.append(("match" if match == 1 else "sub", i - 1, j - 1))
                i -= 1
                j -= 1
                continue
        if i > 0 and (j == 0 or score[i][j] == score[i - 1][j] - 1):
            ops.append(("insert", i - 1, None))
            i -= 1
        else:
            ops.append(("delete", None, j - 1))
            j -= 1
    ops.reverse()

    # --- build units, labels, trace ---
    units: list[Unit] = []
    labels: list[WordLabel] = []
    trace_steps: list[TraceStep] = []
    asr_records: list[AsrWord] = []
    asr_index_map: dict[int, int] = {}  # whisper word idx -> asr_words idx

    for w_idx, w in enumerate(whisper_words):
        start = int((w.get("start") or 0) * 1000)
        end_raw = w.get("end")
        end = int(end_raw * 1000) if end_raw is not None else start + 200
        asr_idx = len(asr_records)
        asr_index_map[w_idx] = asr_idx
        asr_records.append(
            AsrWord(
                index=asr_idx,
                text=w["text"].strip(),
                start_ms=start,
                end_ms=end,
                score=0.9,
            )
        )

    for op, asr_i, pass_j in ops:
        if op in ("match", "sub"):
            assert asr_i is not None and pass_j is not None
            w = whisper_words[asr_i]
            start = int((w.get("start") or 0) * 1000)
            end_raw = w.get("end")
            end = int(end_raw * 1000) if end_raw is not None else start + 200
            pt = passage_tokens[pass_j]
            key = pt.key
            surface = w["text"].strip()
            label = WordLabel.CORRECT if op == "match" else WordLabel.SUBSTITUTION
            units.append(
                Unit(
                    surface=surface,
                    key=key,
                    passage_index=pass_j,
                    passage_span=1,
                    source_indices=(asr_index_map[asr_i],),
                    start_ms=start,
                    end_ms=end,
                    align_score=1.0 if op == "match" else 0.6,
                )
            )
            labels.append(label)
            trace_op = AlignOp.MATCH if op == "match" else AlignOp.SUB
            trace_steps.append(
                TraceStep(
                    op=trace_op,
                    passage_index=pass_j,
                    asr_index=asr_index_map[asr_i],
                )
            )
        elif op == "insert":
            assert asr_i is not None
            w = whisper_words[asr_i]
            start = int((w.get("start") or 0) * 1000)
            end_raw = w.get("end")
            end = int(end_raw * 1000) if end_raw is not None else start + 200
            units.append(
                Unit(
                    surface=w["text"].strip(),
                    key=_key(w["text"]),
                    passage_index=None,
                    passage_span=0,
                    source_indices=(asr_index_map[asr_i],),
                    start_ms=start,
                    end_ms=end,
                    align_score=0.0,
                )
            )
            labels.append(WordLabel.INSERTION)
            trace_steps.append(
                TraceStep(
                    op=AlignOp.INSERT,
                    passage_index=None,
                    asr_index=asr_index_map[asr_i],
                )
            )
        else:  # delete -> OMISSION
            assert pass_j is not None
            units.append(
                Unit(
                    surface="",
                    key=passage_tokens[pass_j].key,
                    passage_index=pass_j,
                    passage_span=1,
                    source_indices=(),
                    start_ms=None,
                    end_ms=None,
                    align_score=0.0,
                )
            )
            labels.append(WordLabel.OMISSION)
            trace_steps.append(TraceStep(op=AlignOp.DELETE, passage_index=pass_j, asr_index=None))

    return LaneAResult(
        units=tuple(units),
        labels=tuple(labels),
        trace=tuple(trace_steps),
        asr_words=tuple(asr_records),
    )


# ---------------------------------------------------------------------------
# Teacher labels (optional, for accuracy computation)
# ---------------------------------------------------------------------------


def load_teacher_labels(path: Path) -> dict[tuple[str, int], str]:
    """CSV: recording,word_index,passage_word,label (0-indexed word_index)."""
    out: dict[tuple[str, int], str] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[(row["recording"], int(row["word_index"]))] = row["label"]
    return out


# ---------------------------------------------------------------------------
# Per-recording run
# ---------------------------------------------------------------------------


@dataclass
class WordRow:
    recording: str
    unit_index: int
    passage_word: str  # text word, or "-" for insertion
    transcribed_word: str  # heard ASR word, or "-" for omission
    stage3_verdict: str  # stage 3 alignment verdict
    verdict: str  # final verdict ("match", "substitution", "mispronunciation", etc.)
    miscue: str  # "yes" if error/miscue, "" if match
    start_s: str  # e.g. "27.28" or "-"
    end_s: str  # e.g. "27.86" or "-"
    phonetic_check: str  # "done", "skipped", "unverifiable"
    d_exp: float | None
    d_hyp: float | None
    expected_ipa: str
    heard_ipa: str
    deviations: str
    status_note: str  # "salvaged" (sub->match), "flagged" (match->mispron), etc.


@dataclass
class RecordingResult:
    wav_name: str
    passage_name: str
    recognise_s: float
    n_phonemes: int
    words: list[WordRow] = field(default_factory=list)
    lane_a_built_from: str = "whisper_cache"


def _format_verdict(label: WordLabel) -> str:
    """Format verdict to match familiar evaluation terminology."""
    if label == WordLabel.CORRECT:
        return "match"
    return label.value


def _format_seconds(ms: int | None) -> str:
    if ms is None:
        return "-"
    return f"{ms / 1000:.2f}"


def run_recording(
    wav: Path,
    rec: Wav2Vec2PhonemeRecognizer,
    preparer: PassagePreparer,
    hyp_preparer: PassagePreparer,
    settings: AudioSettings,
    teacher: dict | None = None,
    asr_engine: str = "crisperwhisper",
) -> RecordingResult:
    passage_text = passage_for(wav)
    passage = preparer.prepare(passage_text)

    # B2: recognise phonemes
    t0 = time.perf_counter()
    phoneme_tokens = rec.recognise(wav, deadline_ms=None)
    recognise_s = time.perf_counter() - t0

    # Build synthetic LaneAResult from CrisperWhisper or Whisper cache
    asr_words, source_name = _load_asr_words(wav, asr_engine)
    if not asr_words:
        logger.warning("%s: no ASR cache; all passage words treated as omissions", wav.name)
        units = []
        for i, token in enumerate(passage.tokens):
            units.append(
                Unit(
                    surface=token.raw,
                    key=token.key,
                    passage_index=i,
                    passage_span=1,
                    source_indices=(),
                    start_ms=None,
                    end_ms=None,
                    align_score=0.0,
                )
            )
        lane_a = LaneAResult(
            units=tuple(units),
            labels=tuple(WordLabel.CORRECT for _ in units),
            trace=tuple(
                TraceStep(op=AlignOp.MATCH, passage_index=i, asr_index=None)
                for i in range(len(units))
            ),
            asr_words=(),
        )
    else:
        lane_a = _build_lane_a(passage, asr_words)

    # B3: join phonemes to units
    join_result = join(
        lane_a.units,
        phoneme_tokens,
        passage,
        pad_ms=settings.JOIN_PAD_MS,
        coverage_min_ms=settings.COVERAGE_MIN_MS,
        coverage_max_gap_ms=settings.COVERAGE_MAX_GAP_MS,
    )

    # B5: arbitrate (includes B4 compare internally)
    hyp = hypothesis_variants(lane_a.units, hyp_preparer)
    arb = arbitrate(
        lane_a,
        passage,
        join_result,
        hyp,
        t_flag=settings.T_FLAG,
        t_overrule=settings.T_OVERRULE,
    )

    result = RecordingResult(
        wav_name=wav.name,
        passage_name=wav.stem,
        recognise_s=recognise_s,
        n_phonemes=len(phoneme_tokens),
        lane_a_built_from=source_name,
    )

    # Calculate omission timing gaps if possible
    for i, (verdict, unit) in enumerate(zip(arb.verdicts, lane_a.units, strict=True)):
        pt = passage.tokens[unit.passage_index] if unit.passage_index is not None else None
        passage_word = pt.raw if pt else "-"
        transcribed_word = unit.surface if unit.surface else "-"

        final_v = _format_verdict(verdict.label)
        stage3_v = _format_verdict(verdict.stage3_label)
        is_miscue = "yes" if verdict.label != WordLabel.CORRECT else ""

        # Note whether Lane B changed the verdict
        if verdict.stage3_label != verdict.label:
            if verdict.label == WordLabel.CORRECT:
                status_note = "salvaged"  # salvaged to match
            else:
                status_note = f"flagged ({final_v})"
        elif verdict.phonetic_check.value == "done":
            status_note = "verified"
        else:
            status_note = verdict.phonetic_check.value

        # Omission fallback timing: look for surrounding times
        start_ms = unit.start_ms
        end_ms = unit.end_ms
        if start_ms is None and verdict.label == WordLabel.OMISSION:
            prev_timed = next(
                (u.end_ms for u in reversed(lane_a.units[:i]) if u.end_ms is not None), None
            )
            next_timed = next(
                (u.start_ms for u in lane_a.units[i + 1 :] if u.start_ms is not None), None
            )
            if prev_timed is not None and next_timed is not None:
                start_ms, end_ms = prev_timed, next_timed

        expected_variants = verdict.expected_phonemes
        best_idx = verdict.best_variant_index
        best_variant = (
            expected_variants[best_idx] if best_idx is not None and expected_variants else ()
        )

        row = WordRow(
            recording=wav.stem,
            unit_index=i,
            passage_word=passage_word,
            transcribed_word=transcribed_word,
            stage3_verdict=stage3_v,
            verdict=final_v,
            miscue=is_miscue,
            start_s=_format_seconds(start_ms),
            end_s=_format_seconds(end_ms),
            phonetic_check=verdict.phonetic_check.value,
            d_exp=(
                round(verdict.distance_expected, 3)
                if verdict.distance_expected is not None
                else None
            ),
            d_hyp=(
                round(verdict.distance_hypothesis, 3)
                if verdict.distance_hypothesis is not None
                else None
            ),
            expected_ipa=" ".join(best_variant) if best_variant else "-",
            heard_ipa=" ".join(verdict.heard_phonemes) if verdict.heard_phonemes else "-",
            deviations=", ".join(f"{e or 'none'}->{h or 'none'}" for e, h in verdict.deviations),
            status_note=status_note,
        )
        result.words.append(row)

    return result


# ---------------------------------------------------------------------------
# Reporting (Console, Markdown, and Interactive HTML)
# ---------------------------------------------------------------------------


def print_recording_table(r: RecordingResult, detailed: bool = True) -> None:
    """Print clean aligned table matching the user's reference format."""
    total = len(r.words)
    verified = sum(1 for w in r.words if w.phonetic_check == "done")
    miscues = sum(1 for w in r.words if w.miscue == "yes")
    changes = sum(1 for w in r.words if w.stage3_verdict != w.verdict)

    print()
    print("=" * 115)
    print(f"  RECORDING: {r.wav_name}")
    print(
        f"  Passage: {r.passage_name} | Phoneme Inference: {r.recognise_s:.2f}s | "
        f"Units: {total} | Verified: {verified} | Miscues: {miscues} | Changes: {changes}"
    )
    print("=" * 115)

    if not detailed:
        # Exact compact table matching the image
        print(
            f"  {'#':>3}  {'passage':16}  {'transcribed':16}  {'verdict':16}  "
            f"{'miscue':6}  {'start':>7}  {'end':>7}"
        )
        print(f"  {'-' * 3}  {'-' * 16}  {'-' * 16}  {'-' * 16}  {'-' * 6}  {'-' * 7}  {'-' * 7}")
        for w in r.words:
            print(
                f"  {w.unit_index:>3}  {w.passage_word:16}  {w.transcribed_word:16}  "
                f"{w.verdict:16}  {w.miscue:6}  {w.start_s:>7}  {w.end_s:>7}"
            )
    else:
        # Full table with timings, verdict, miscue flag, and phonetic details
        hdr = (
            f"  {'#':>3}  {'passage':15}  {'transcribed':15}  {'verdict':16}  "
            f"{'miscue':6}  {'start':>7}  {'end':>7}  {'d_exp':>6}  "
            f"{'phonetic (exp -> heard)':<28}  status"
        )
        print(hdr)
        print(
            f"  {'-' * 3}  {'-' * 15}  {'-' * 15}  {'-' * 16}  "
            f"{'-' * 6}  {'-' * 7}  {'-' * 7}  {'-' * 6}  {'-' * 28}  {'-' * 12}"
        )
        for w in r.words:
            d_exp_str = f"{w.d_exp:.3f}" if w.d_exp is not None else "     -"
            ipa_str = (
                f"{w.expected_ipa} -> {w.heard_ipa}"
                if w.expected_ipa != "-" or w.heard_ipa != "-"
                else "-"
            )
            if len(ipa_str) > 28:
                ipa_str = ipa_str[:25] + "..."
            mark = "*" if w.stage3_verdict != w.verdict else " "
            print(
                f" {mark}{w.unit_index:>3}  {w.passage_word:15}  {w.transcribed_word:15}  "
                f"{w.verdict:16}  {w.miscue:6}  {w.start_s:>7}  {w.end_s:>7}  "
                f"{d_exp_str:>6}  {ipa_str:<28}  {w.status_note}"
            )
    print("-" * 115)


def write_markdown(results: list[RecordingResult], out_path: Path) -> None:
    lines: list[str] = [
        "# Lane B Real Recording Test Results",
        "",
        "Generated by `devtools/run_lane_b.py` using real recordings from `audio/data/`.",
        "Columns align with the system evaluation standard: `#`, `passage`, `transcribed`, "
        "`verdict`, `miscue`, `start`, `end`, and phonetic verification details.",
        "",
    ]

    for r in results:
        total = len(r.words)
        verified = sum(1 for w in r.words if w.phonetic_check == "done")
        miscues = sum(1 for w in r.words if w.miscue == "yes")
        changes = sum(1 for w in r.words if w.stage3_verdict != w.verdict)

        lines += [
            f"## `{r.wav_name}`",
            "",
            f"- **Passage:** `{r.passage_name}`",
            f"- **Phoneme inference:** {r.recognise_s:.2f} s ({r.n_phonemes} phonemes detected)",
            f"- **Total units:** {total} | **Verified:** {verified} | **Miscues:** {miscues} | "
            f"**Lane B label changes:** {changes}",
            "",
            "| # | passage | transcribed | verdict | miscue | start | end | d_exp | "
            "expected IPA | heard IPA | deviations | status |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for w in r.words:
            changed_mark = "\\*" if w.stage3_verdict != w.verdict else ""
            d_exp_str = f"{w.d_exp:.3f}" if w.d_exp is not None else "—"
            lines.append(
                f"| {changed_mark}{w.unit_index} | {w.passage_word} | {w.transcribed_word} | "
                f"`{w.verdict}` | {w.miscue} | {w.start_s} | {w.end_s} | {d_exp_str} | "
                f"`{w.expected_ipa}` | `{w.heard_ipa}` | {w.deviations} | {w.status_note} |"
            )
        lines.append("")

    all_words = [w for r in results for w in r.words]
    verified_words = [w for w in all_words if w.phonetic_check == "done"]
    d_vals = [w.d_exp for w in verified_words if w.d_exp is not None]

    lines += [
        "## Summary Statistics Across All Recordings",
        "",
        f"- **Total recordings:** {len(results)}",
        f"- **Total word units:** {len(all_words)}",
        f"- **Phonetically verified units:** {len(verified_words)} / {len(all_words)}",
    ]
    if d_vals:
        import statistics

        lines += [
            f"- **Phoneme distance to expected (d_exp) on verified words:** "
            f"Median = {statistics.median(d_vals):.3f}, Mean = {sum(d_vals) / len(d_vals):.3f}",
        ]

    counts: dict[str, int] = {}
    for w in all_words:
        counts[w.verdict] = counts.get(w.verdict, 0) + 1

    lines += [
        "",
        "### Final Verdict Breakdown",
        "",
        "| verdict | count | percentage |",
        "|---|---|---|",
        *[
            f"| `{k}` | {v} | {v / len(all_words) * 100:.1f}% |"
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
    ]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Markdown written to {out_path}")


def write_interactive_html(results: list[RecordingResult], out_path: Path) -> None:
    """Generate an interactive HTML report with audio player, filters, and color badges."""
    style = (
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; "
        "margin: 24px; background: #0f1117; color: #e1e4ea; }\n"
        "h1, h2 { color: #f0f3f6; }\n"
        ".badge { display: inline-block; padding: 2px 8px; border-radius: 4px; "
        "font-weight: 600; font-size: 12px; }\n"
        ".match { background: #163c26; color: #4ade80; border: 1px solid #238636; }\n"
        ".mispronunciation { background: #3b2314; color: #fb923c; border: 1px solid #9a3412; }\n"
        ".substitution { background: #3c1626; color: #f472b6; border: 1px solid #9d174d; }\n"
        ".omission { background: #361717; color: #f87171; border: 1px solid #b91c1c; }\n"
        ".insertion { background: #1e293b; color: #94a3b8; border: 1px solid #475569; }\n"
        ".yes { color: #f87171; font-weight: bold; }\n"
        "table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }\n"
        "th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #2d333b; }\n"
        "th { background: #1c2128; color: #8b949e; position: sticky; top: 0; }\n"
        "tr:hover { background: #161b22; }\n"
        ".ipa { font-family: monospace; color: #79c0ff; }\n"
        ".header-card { background: #161b22; border: 1px solid #30363d; "
        "border-radius: 6px; padding: 16px; margin-bottom: 20px; }\n"
    )
    desc = (
        "<p>End-to-end results on real recordings comparing passage reference, "
        "ASR transcription, timestamps, and phonetic arbitration.</p>\n"
    )
    html = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n<meta charset='UTF-8'>\n"
        "<title>Lane B Evaluation Results</title>\n<style>\n"
        + style
        + "</style>\n</head>\n<body>\n<h1>Lane B Phonetic Evaluation Report</h1>\n"
        + desc
    )
    for r in results:
        total = len(r.words)
        verified = sum(1 for w in r.words if w.phonetic_check == "done")
        miscues = sum(1 for w in r.words if w.miscue == "yes")
        info = (
            f"Passage: <strong>{r.passage_name}</strong> | "
            f"Phoneme inference: <strong>{r.recognise_s:.2f}s</strong> | "
            f"Total units: <strong>{total}</strong> | "
            f"Verified: <strong>{verified}</strong> | "
            f"Miscues: <strong>{miscues}</strong>"
        )
        html += f"""
<div class="header-card">
  <h2>Recording: {r.wav_name}</h2>
  <div>{info}</div>
</div>
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>Passage</th>
      <th>Transcribed</th>
      <th>Verdict</th>
      <th>Miscue</th>
      <th>Start (s)</th>
      <th>End (s)</th>
      <th>d_exp</th>
      <th>Expected IPA</th>
      <th>Heard IPA</th>
      <th>Deviations</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
"""
        for w in r.words:
            v_cls = w.verdict
            miscue_cls = "yes" if w.miscue == "yes" else ""
            html += f"""    <tr>
      <td>{w.unit_index}</td>
      <td><strong>{w.passage_word}</strong></td>
      <td>{w.transcribed_word}</td>
      <td><span class="badge {v_cls}">{w.verdict}</span></td>
      <td class="{miscue_cls}">{w.miscue}</td>
      <td>{w.start_s}</td>
      <td>{w.end_s}</td>
      <td>{w.d_exp if w.d_exp is not None else "-"}</td>
      <td class="ipa">{w.expected_ipa}</td>
      <td class="ipa">{w.heard_ipa}</td>
      <td>{w.deviations}</td>
      <td>{w.status_note}</td>
    </tr>\n"""
        html += "  </tbody>\n</table>\n"

    html += "</body>\n</html>"
    out_path.write_text(html, encoding="utf-8")
    print(f"Interactive HTML written to {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Lane B on real WAVs in audio/data/ and output aligned timestamps"
    )
    parser.add_argument("--labels", metavar="CSV", help="optional teacher-label CSV for accuracy")
    parser.add_argument(
        "--wav", metavar="WAV", help="run on one specific WAV only (filename or path)"
    )
    parser.add_argument(
        "--asr",
        choices=["crisperwhisper", "whisper"],
        default="crisperwhisper",
        help="ASR engine to use for word boundaries (default: crisperwhisper)",
    )
    parser.add_argument("--device", default="auto", help="cpu / cuda / auto (default: auto)")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="output compact table (#, passage, transcribed, verdict, miscue, start, end)",
    )
    parser.add_argument(
        "--no-whisper-transcribe",
        action="store_true",
        help="skip running Whisper even if cache is missing",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = AudioSettings(_env_file=None)

    # --- collect WAVs ---
    data_dir = ROOT / "data"
    if args.wav:
        wav_path = Path(args.wav)
        if not wav_path.is_absolute():
            wav_path = data_dir / wav_path
        wavs = [wav_path]
    else:
        wavs = sorted(data_dir.glob("*.wav"))
    if not wavs:
        print(f"No WAV files found in {data_dir}")
        sys.exit(1)

    # --- load recogniser ---
    device = (
        args.device
        if args.device != "auto"
        else ("cuda" if __import__("torch").cuda.is_available() else "cpu")
    )
    print(f"Loading phoneme recogniser ({settings.PHONEME_MODEL}) on {device}...")
    t0 = time.perf_counter()
    recognizer = Wav2Vec2PhonemeRecognizer(
        settings.PHONEME_MODEL,
        device,
        settings.PHONEME_FP16,
        settings.PHONEME_CHUNK_S,
        settings.PHONEME_CONTEXT_S,
    )
    print(f"Loaded recogniser in {time.perf_counter() - t0:.1f}s")

    # --- G2P / preparer ---
    g2p = build_g2p(settings)
    preparer = PassagePreparer(g2p, cache_size=settings.PASSAGE_CACHE_SIZE)
    hyp_preparer = PassagePreparer(g2p, cache_size=0)

    # --- teacher labels (optional) ---
    teacher = load_teacher_labels(Path(args.labels)) if args.labels else None

    # --- run on recordings ---
    results: list[RecordingResult] = []
    for wav in wavs:
        try:
            r = run_recording(
                wav,
                recognizer,
                preparer,
                hyp_preparer,
                settings,
                teacher,
                asr_engine=args.asr,
            )
            results.append(r)
            print_recording_table(r, detailed=not args.compact)
        except Exception as exc:
            logger.exception("error processing %s: %s", wav.name, exc)

    # --- output files ---
    OUT.mkdir(parents=True, exist_ok=True)
    write_markdown(results, OUT / "lane_b_results.md")
    write_interactive_html(results, OUT / "lane_b_report.html")


if __name__ == "__main__":
    main()
