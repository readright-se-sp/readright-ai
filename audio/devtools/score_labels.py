"""Score Lane A alone and Lane B settings against the hand labels, from the cache (no models).

Labels: tests/fixtures/labels_b6b.csv (plans/phase-B6b.md). A row is a *counted error* when its
tag is in COUNTED_TAGS; for the labels, `transposition` counts too. Per split, over all rows:

  TP = counted error in the final tag and in the label    FP = counted in the tag only
  FN = counted in the label only                          exact = final tag equals the label

Every number is also given without the rows whose notes say `unsure`.

Selection rule (fixed in the plan, on the SELECTION recordings only):
  1. fewest FP + FN;  2. fewer FN;  3. higher exact-tag agreement;  4. closest to the defaults.
The held-out recordings are only reported. The shipped config is the user's pick after the
plan change of 2026-11-03 (the rule picks T_FLAG 0.30 with the unsure rows, 0.25 without);
it is the SHIPPED dict below and is reported next to the rule's pick.

    uv run python devtools/score_labels.py      # writes devtools/out/b6b_sweep.csv and .md
"""

import csv
import itertools
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "devtools"))

from replay import Cached, Replayer, load_all, with_overrides  # noqa: E402
from sweep_join import is_held_out  # noqa: E402

from audio.config import AudioSettings  # noqa: E402
from audio.models import COUNTED_TAGS  # noqa: E402

LABELS_CSV = ROOT / "tests" / "fixtures" / "labels_b6b.csv"
OUT_CSV = ROOT / "devtools" / "out" / "b6b_sweep.csv"
OUT_MD = ROOT / "devtools" / "out" / "b6b_sweep.md"

SPLITS = ("selection", "heldout")
# The settings the selection rule's last step measures distance from: the B6a values, with
# OVERRULE_MATCH false (plan Q2).
REFERENCE: dict[str, Any] = {
    "OVERRULE_MATCH": False,
    "T_FLAG": 0.25,
    "T_FLAG_INCLUSIVE": True,
    "T_OVERRULE": 0.45,
    "COVERAGE_GUARD_RATIO": 1.0,
}


# What config.py ships (user decision, plans/phase-B6b.md "Plan change").
SHIPPED: dict[str, Any] = {**REFERENCE, "T_FLAG_INCLUSIVE": False}


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact: int = 0
    rows: int = 0

    @property
    def errors(self) -> int:
        return self.fp + self.fn


@dataclass(frozen=True)
class Label:
    label: str
    passage_word: str
    split: str
    unsure: bool


def load_labels(path: Path = LABELS_CSV) -> dict[tuple[str, int], Label]:
    with path.open(encoding="utf-8", newline="") as f:
        return {
            (r["recording"], int(r["row"])): Label(
                r["label"], r["passage_word"], r["split"], "unsure" in r["notes"].lower()
            )
            for r in csv.DictReader(f)
        }


def is_error_label(label: str) -> bool:
    return label in COUNTED_TAGS or label == "transposition"


def exact(tag: str, label: str) -> bool:
    return tag == label or (label == "transposition" and tag == "letter_transposition")


def score_run(
    c: Cached, words: Sequence[Mapping[str, Any]], labels: dict[tuple[str, int], Label]
) -> dict[tuple[str, bool], Counts]:
    """Counts for one recording, keyed by (split, without_unsure)."""
    out: dict[tuple[str, bool], Counts] = {}
    split = "heldout" if is_held_out(c) else "selection"
    for i, w in enumerate(words, start=1):
        lab = labels.get((c.recording, i))
        if lab is None:
            raise ValueError(f"{c.recording} row {i}: no label")
        if (w["passage_word"] or "") != lab.passage_word:
            raise ValueError(
                f"{c.recording} row {i}: passage_word {w['passage_word']!r} != {lab.passage_word!r}"
            )
        if lab.split != split:
            raise ValueError(f"{c.recording} row {i}: split {lab.split} != {split}")
        pred, true = w["tag"] in COUNTED_TAGS, is_error_label(lab.label)
        for without in (False, True):
            if without and lab.unsure:
                continue
            n = out.setdefault((split, without), Counts())
            n.rows += 1
            n.tp += pred and true
            n.fp += pred and not true
            n.fn += true and not pred
            n.exact += exact(w["tag"], lab.label)
    if len(words) != sum(1 for k in labels if k[0] == c.recording):
        raise ValueError(f"{c.recording}: {len(words)} rows, labels have another count")
    return out


def score_config(
    replayer: Replayer,
    caches: list[Cached],
    labels: dict[tuple[str, int], Label],
    cfg: dict[str, Any] | None,
) -> tuple[dict[tuple[str, bool], Counts], dict[str, tuple[int, int]]]:
    """Totals per (split, without_unsure) and per recording (final counted errors, labelled)."""
    settings = with_overrides(replayer.base, **cfg) if cfg is not None else None
    total: dict[tuple[str, bool], Counts] = {}
    per_rec: dict[str, tuple[int, int]] = {}
    for c in caches:
        result = replayer.run(c, settings, phonetic=cfg is not None)
        for key, n in score_run(c, result["words"], labels).items():
            t = total.setdefault(key, Counts())
            t.tp, t.fp, t.fn = t.tp + n.tp, t.fp + n.fp, t.fn + n.fn
            t.exact, t.rows = t.exact + n.exact, t.rows + n.rows
        n_final = sum(w["tag"] in COUNTED_TAGS for w in result["words"])
        n_label = sum(
            is_error_label(lab.label) for (rec, _), lab in labels.items() if rec == c.recording
        )
        per_rec[c.recording] = (n_final, n_label)
    return total, per_rec


def grid() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for overrule, t_flag, inclusive, t_over in itertools.product(
        (False, True), (0.15, 0.20, 0.25, 0.30), (False, True), (0.35, 0.45, 0.55)
    ):
        for guard in (0.8, 1.0) if overrule else (1.0,):
            configs.append(
                {
                    "OVERRULE_MATCH": overrule,
                    "T_FLAG": t_flag,
                    "T_FLAG_INCLUSIVE": inclusive,
                    "T_OVERRULE": t_over,
                    "COVERAGE_GUARD_RATIO": guard,
                }
            )
    return configs


def name(cfg: dict[str, Any]) -> str:
    op = "<=" if cfg["T_FLAG_INCLUSIVE"] else "<"
    m = "overrule" if cfg["OVERRULE_MATCH"] else "keep"
    return (
        f"{m} flag{op}{cfg['T_FLAG']:.2f} over{cfg['T_OVERRULE']:.2f}"
        f" guard{cfg['COVERAGE_GUARD_RATIO']}"
    )


def distance_from_reference(cfg: dict[str, Any]) -> tuple[int, float]:
    differing = sum(cfg[k] != v for k, v in REFERENCE.items())
    gap = abs(cfg["T_FLAG"] - 0.25) + abs(cfg["T_OVERRULE"] - 0.45)
    return differing, gap


def select(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The plan's selection rule, on the selection-split numbers (with the unsure rows)."""
    return min(
        rows,
        key=lambda r: (
            r["selection_fp"] + r["selection_fn"],
            r["selection_fn"],
            -r["selection_exact"],
            distance_from_reference(r),
            r["name"],
        ),
    )


def flat(total: dict[tuple[str, bool], Counts]) -> dict[str, int]:
    out: dict[str, int] = {}
    for (split, without), n in total.items():
        p = f"{split}_{'nu_' if without else ''}"
        out |= {
            f"{p}tp": n.tp,
            f"{p}fp": n.fp,
            f"{p}fn": n.fn,
            f"{p}exact": n.exact,
            f"{p}rows": n.rows,
        }
    return out


def fmt(total: dict[tuple[str, bool], Counts], split: str, without: bool = False) -> str:
    n = total[(split, without)]
    return f"TP {n.tp} / FP {n.fp} / FN {n.fn}, exact {n.exact}/{n.rows}"


def main() -> None:
    caches = load_all()
    if not caches:
        sys.exit("No cache: run devtools/cache_runs.py first")
    labels = load_labels()
    replayer = Replayer(AudioSettings(_env_file=None))

    lane_a, lane_a_rec = score_config(replayer, caches, labels, None)
    rows: list[dict[str, Any]] = []
    for cfg in grid():
        total, _ = score_config(replayer, caches, labels, cfg)
        rows.append({**cfg, "name": name(cfg), **flat(total)})
    base = {"name": "Lane A alone", **flat(lane_a)}
    chosen = select(rows)
    chosen_total, chosen_rec = score_config(
        replayer,
        caches,
        labels,
        {k: chosen[k] for k in REFERENCE},
    )

    # Robustness: configs that beat Lane A alone on held-out FP + FN (and on selection).
    def err(r: dict[str, Any], split: str, p: str = "") -> int:
        return r[f"{split}_{p}fp"] + r[f"{split}_{p}fn"]

    beat_held = [r for r in rows if err(r, "heldout") < err(base, "heldout")]
    beat_both = [r for r in beat_held if err(r, "selection") < err(base, "selection")]
    # The same selection rule without the unsure rows: does the choice move?
    chosen_nu = min(
        rows,
        key=lambda r: (
            err(r, "selection", "nu_"),
            r["selection_nu_fn"],
            -r["selection_nu_exact"],
            distance_from_reference(r),
            r["name"],
        ),
    )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    names = list(rows[0])
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)

    lines = ["# B6b sweep", ""]
    lines.append(f"Configs: {len(rows)}. Rows scored: {sum(len(c.lane_a_rows) for c in caches)}.")
    lines += ["", "## Lane A alone", ""]
    for split in SPLITS:
        lines.append(f"- {split}: {fmt(lane_a, split)}; without unsure: {fmt(lane_a, split, True)}")
    lines += ["", f"## Chosen: `{chosen['name']}`", ""]
    for split in SPLITS:
        both = f"{fmt(chosen_total, split)}; without unsure: {fmt(chosen_total, split, True)}"
        lines.append(f"- {split}: {both}")
    lines.append(f"- the same rule without the unsure rows picks: `{chosen_nu['name']}`")
    shipped = next(r for r in rows if all(r[k] == v for k, v in SHIPPED.items()))
    shipped_total, shipped_rec = score_config(replayer, caches, labels, dict(SHIPPED))
    lines += ["", f"## Shipped: `{shipped['name']}`", ""]
    for split in SPLITS:
        both = f"{fmt(shipped_total, split)}; without unsure: {fmt(shipped_total, split, True)}"
        lines.append(f"- {split}: {both}")
    for c in caches:
        lines.append(
            f"- {c.recording[:10]}: Lane A {lane_a_rec[c.recording][0]}, shipped "
            f"{shipped_rec[c.recording][0]}, labelled {shipped_rec[c.recording][1]}"
        )
    lines += [
        "",
        f"Robustness: {len(beat_held)} of {len(rows)} configs beat Lane A alone on held-out "
        f"FP + FN; {len(beat_both)} beat it on both splits.",
        "",
        "## Counted errors per recording (Lane A alone, chosen, labelled)",
        "",
    ]
    for c in caches:
        lines.append(
            f"- {c.recording[:10]} ({'heldout' if is_held_out(c) else 'selection'}): "
            f"{lane_a_rec[c.recording][0]}, {chosen_rec[c.recording][0]}, "
            f"{chosen_rec[c.recording][1]}"
        )
    lines += ["", "## Top 15 by the selection rule", ""]
    ranked = sorted(
        rows,
        key=lambda r: (
            err(r, "selection"),
            r["selection_fn"],
            -r["selection_exact"],
            distance_from_reference(r),
            r["name"],
        ),
    )
    for r in ranked[:15]:
        lines.append(
            f"- `{r['name']}`: sel FP+FN {err(r, 'selection')} (FN {r['selection_fn']}, exact "
            f"{r['selection_exact']}) | held FP+FN {err(r, 'heldout')} (FN {r['heldout_fn']}, "
            f"exact {r['heldout_exact']})"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
