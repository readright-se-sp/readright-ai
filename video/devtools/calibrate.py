"""Grid-search the main thresholds against a labels CSV and print precision/recall/F1.

Usage: uv run python -m devtools.calibrate <labels.csv> [--cache DIR] [--grid name=v1,v2 ...]

Labels CSV columns: file,is_finger_pointing,is_lack_expression (extra columns ignored).
Each setting is varied alone with the others at their defaults. Prints to stdout only.
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.expression import assess_expression
from app.frames import SamplePlan
from app.landmarks import FrameFeatures
from app.pointing import assess_pointing
from devtools.feature_cache import load_cache

EXPRESSION = "is_lack_expression"
POINTING = "is_finger_pointing"
FLAG_BY_SETTING = {
    "expression_min_range": EXPRESSION,
    "pointing_min_total_ms": POINTING,
    "pointing_extended_max_bend_deg": POINTING,
    "sweep_min_count": POINTING,
    "use_pose": POINTING,
}
DEFAULT_GRID: dict[str, list] = {
    "expression_min_range": [round(0.06 + 0.02 * i, 2) for i in range(8)],
    "pointing_min_total_ms": [1000 + 500 * i for i in range(7)],
    "pointing_extended_max_bend_deg": [20 + 5 * i for i in range(7)],
    "sweep_min_count": [1, 2, 3, 4],
    "use_pose": [False, True],
}
HEADER = ["flag", "setting", "value", "tp", "fp", "tn", "fn", "precision", "recall", "f1"]
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}

Cache = dict[str, tuple[list[FrameFeatures], SamplePlan]]
Labels = dict[str, tuple[bool, bool]]


@dataclass(frozen=True)
class Row:
    flag: str
    setting: str
    value: float | bool
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None


def _parse_bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"not a boolean: {text!r}")


def read_labels(path: Path) -> Labels:
    labels: Labels = {}
    with path.open(newline="", encoding="utf-8") as f:
        for number, record in enumerate(csv.DictReader(f), start=1):
            try:
                name = record["file"].strip()
                if not name:
                    raise ValueError("empty file name")
                flags = (_parse_bool(record[POINTING]), _parse_bool(record[EXPRESSION]))
            except (KeyError, TypeError, AttributeError, ValueError) as err:
                raise ValueError(f"labels row {number}: missing or invalid cell ({err})") from None
            stem = Path(name).stem
            if stem in labels:
                raise ValueError(f"labels row {number}: duplicate file {stem!r}")
            labels[stem] = flags
    return labels


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _predict(
    flag: str, features: list[FrameFeatures], plan: SamplePlan, settings: Settings
) -> bool:
    if flag == EXPRESSION:
        return assess_expression(features, settings).is_lack_expression
    return assess_pointing(features, plan, settings).is_finger_pointing


def evaluate(cache: Cache, labels: Labels, base: Settings, grid: dict[str, list]) -> list[Row]:
    stems = sorted(set(cache) & set(labels))
    rows: list[Row] = []
    for setting, values in grid.items():
        flag = FLAG_BY_SETTING[setting]
        label_index = 1 if flag == EXPRESSION else 0
        for value in values:
            settings = base.model_copy(update={setting: value})
            tp = fp = tn = fn = 0
            for stem in stems:
                features, plan = cache[stem]
                predicted = _predict(flag, features, plan, settings)
                actual = labels[stem][label_index]
                if predicted and actual:
                    tp += 1
                elif predicted:
                    fp += 1
                elif actual:
                    fn += 1
                else:
                    tn += 1
            precision = _ratio(tp, tp + fp)
            recall = _ratio(tp, tp + fn)
            f1 = (
                2 * precision * recall / (precision + recall)
                if precision is not None and recall is not None and precision + recall > 0
                else None
            )
            rows.append(Row(flag, setting, value, tp, fp, tn, fn, precision, recall, f1))
    return rows


def recommend(rows: list[Row], base: Settings, min_precision: float = 0.9) -> dict[str, Row]:
    best: dict[str, Row] = {}
    for setting in dict.fromkeys(r.setting for r in rows):
        group = [r for r in rows if r.setting == setting]
        default = float(getattr(base, setting))
        qualified = [r for r in group if r.precision is not None and r.precision >= min_precision]
        if qualified:
            best[setting] = max(
                qualified,
                key=lambda r: (
                    r.recall or 0.0,
                    r.f1 or 0.0,
                    -abs(float(r.value) - default),
                ),
            )
        else:
            defined = [r for r in group if r.precision is not None]
            if defined:
                best[setting] = max(defined, key=lambda r: r.precision or 0.0)
    return best


def _fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def _parse_grid(specs: list[str], base: Settings) -> dict[str, list]:
    grid = {name: list(values) for name, values in DEFAULT_GRID.items()}
    for spec in specs:
        name, _, text = spec.partition("=")
        if name not in FLAG_BY_SETTING or not text:
            raise ValueError(f"bad --grid {spec!r}; settings: {', '.join(FLAG_BY_SETTING)}")
        kind = type(getattr(base, name))
        parse = _parse_bool if kind is bool else kind
        values = []
        for part in text.split(","):
            try:
                value = parse(part)
                Settings.model_validate({**base.model_dump(), name: value})
            except ValueError as err:
                raise ValueError(f"invalid --grid value {part!r} for {name}: {err}") from None
            values.append(value)
        grid[name] = values
    return grid


def _misclassified(
    row: Row, cache: Cache, labels: Labels, base: Settings, stems: list[str]
) -> list[str]:
    settings = base.model_copy(update={row.setting: row.value})
    index = 1 if row.flag == EXPRESSION else 0
    return [
        stem for stem in stems if _predict(row.flag, *cache[stem], settings) != labels[stem][index]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".feature-cache"))
    parser.add_argument("--grid", action="append", default=[], metavar="NAME=V1,V2")
    args = parser.parse_args(argv)

    base = Settings()
    try:
        grid = _parse_grid(args.grid, base)
        labels = read_labels(args.labels)
    except (ValueError, KeyError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    cache: Cache = {}
    unreadable: list[str] = []
    for path in sorted(args.cache.glob("*.npz")):
        try:
            cache[path.stem] = load_cache(path)
        except Exception:
            unreadable.append(path.stem)

    missing = sorted(set(labels) - set(cache))
    unlabeled = sorted(set(cache) - set(labels))
    stems = sorted(set(cache) & set(labels))
    if not stems:
        print("no file is in both the labels and the cache")
        return 2

    if not any(f.pose is not None for s in stems for f in cache[s][0]):
        grid.pop("use_pose", None)
        print("note: cache has no pose data; use_pose skipped")
    rows = evaluate(cache, labels, base, grid)
    print(",".join(HEADER))
    for r in rows:
        print(
            f"{r.flag},{r.setting},{r.value},{r.tp},{r.fp},{r.tn},{r.fn},"
            f"{_fmt(r.precision)},{_fmt(r.recall)},{_fmt(r.f1)}"
        )
    print()
    print("missing from cache:")
    for stem in missing:
        print(f"  {stem}")
    print("unlabeled in cache:")
    for stem in unlabeled:
        print(f"  {stem}")
    print("unreadable cache:")
    for stem in unreadable:
        print(f"  {stem}")
    print()
    best = recommend(rows, base)
    for setting in dict.fromkeys(r.setting for r in rows):
        row = best.get(setting)
        if row is None:
            print(f"{setting}: no recommendation (precision undefined at every value)")
            continue
        wrong = _misclassified(row, cache, labels, base, stems)
        print(
            f"recommended {setting} = {row.value} "
            f"(precision {_fmt(row.precision)}, recall {_fmt(row.recall)})"
        )
        print(f"  misclassified: {', '.join(wrong) if wrong else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
