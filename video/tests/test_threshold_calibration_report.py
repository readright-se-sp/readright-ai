"""Calibration grid search: confusion counts, scores, labels, missing files, recommendation."""

import csv
import io
from pathlib import Path

import pytest
from devtool_helpers import default_settings
from expression_helpers import block_step, no_face_frames
from pointing_helpers import hands_in_range, make_features

from app.frames import SamplePlan
from devtools import calibrate
from devtools.calibrate import Row, evaluate, read_labels, recommend
from devtools.feature_cache import save_cache

PLAN = SamplePlan(fps=5.0, width=640, height=480, max_frames=900)
HEADER = ["flag", "setting", "value", "tp", "fp", "tn", "fn", "precision", "recall", "f1"]


def expression_cache() -> dict:
    """Group variability per file is the step: 0, 0.08, 0.15, 0.15."""
    return {
        "a": (block_step("smile", 0.0), PLAN),
        "b": (block_step("smile", 0.08), PLAN),
        "c": (block_step("smile", 0.15), PLAN),
        "d": (block_step("smile", 0.15), PLAN),
    }


EXPRESSION_LABELS = {
    "a": (False, True),
    "b": (False, True),
    "c": (False, False),
    "d": (False, True),
}


def pointing_cache() -> dict:
    """Pointing total: none, about 4 s, about 1.5 s, about 1.5 s."""
    return {
        "p0": (make_features(100), PLAN),
        "p1": (make_features(100, hands_in_range(10, 30)), PLAN),
        "p2": (make_features(100, hands_in_range(10, 18)), PLAN),
        "p3": (make_features(100, hands_in_range(10, 18)), PLAN),
    }


POINTING_LABELS = {
    "p0": (False, False),
    "p1": (True, False),
    "p2": (True, False),
    "p3": (False, False),
}


def by_value(rows: list[Row]) -> dict:
    return {row.value: row for row in rows}


def counts(row: Row) -> tuple[int, int, int, int]:
    return row.tp, row.fp, row.tn, row.fn


def test_reports_expression_counts_and_scores_for_each_threshold_value() -> None:
    """Criterion 1."""
    grid = {"expression_min_range": [0.04, 0.10, 0.20]}
    rows = evaluate(expression_cache(), EXPRESSION_LABELS, default_settings(), grid)
    assert len(rows) == 3
    assert {row.flag for row in rows} == {"is_lack_expression"}
    assert {row.setting for row in rows} == {"expression_min_range"}
    rows_by_value = by_value(rows)

    low = rows_by_value[0.04]
    assert counts(low) == (1, 0, 1, 2)
    assert low.precision == pytest.approx(1.0)
    assert low.recall == pytest.approx(1 / 3)
    assert low.f1 == pytest.approx(0.5)

    mid = rows_by_value[0.10]
    assert counts(mid) == (2, 0, 1, 1)
    assert mid.precision == pytest.approx(1.0)
    assert mid.recall == pytest.approx(2 / 3)
    assert mid.f1 == pytest.approx(0.8)

    high = rows_by_value[0.20]
    assert counts(high) == (3, 1, 0, 0)
    assert high.precision == pytest.approx(0.75)
    assert high.recall == pytest.approx(1.0)
    assert high.f1 == pytest.approx(6 / 7)


def test_reports_pointing_counts_and_scores_for_each_threshold_value() -> None:
    """Criterion 1."""
    grid = {"pointing_min_total_ms": [1000, 3000, 6000]}
    rows = evaluate(pointing_cache(), POINTING_LABELS, default_settings(), grid)
    assert {row.flag for row in rows} == {"is_finger_pointing"}
    rows_by_value = by_value(rows)

    loose = rows_by_value[1000]
    assert counts(loose) == (2, 1, 1, 0)
    assert loose.precision == pytest.approx(2 / 3)
    assert loose.recall == pytest.approx(1.0)
    assert loose.f1 == pytest.approx(0.8)

    middle = rows_by_value[3000]
    assert counts(middle) == (1, 0, 2, 1)
    assert middle.precision == pytest.approx(1.0)
    assert middle.recall == pytest.approx(0.5)
    assert middle.f1 == pytest.approx(2 / 3)


def test_precision_is_undefined_when_nothing_is_predicted_positive() -> None:
    """Criterion 1."""
    grid = {"pointing_min_total_ms": [6000]}
    (row,) = evaluate(pointing_cache(), POINTING_LABELS, default_settings(), grid)
    assert counts(row) == (0, 0, 2, 2)
    assert row.precision is None
    assert row.recall == pytest.approx(0.0)


def test_recall_is_undefined_when_no_file_is_labeled_positive() -> None:
    """Criterion 1."""
    labels = {stem: (False, False) for stem in POINTING_LABELS}
    grid = {"pointing_min_total_ms": [1000]}
    (row,) = evaluate(pointing_cache(), labels, default_settings(), grid)
    assert counts(row) == (0, 3, 1, 0)
    assert row.precision == pytest.approx(0.0)
    assert row.recall is None
    assert row.f1 is None


def test_varies_one_setting_at_a_time_with_others_at_base() -> None:
    """Criterion 1."""
    grid = {"expression_min_range": [0.04, 0.10], "pointing_min_total_ms": [1000, 3000, 6000]}
    cache = {**expression_cache(), **pointing_cache()}
    labels = {**EXPRESSION_LABELS, **POINTING_LABELS}
    rows = evaluate(cache, labels, default_settings(), grid)
    assert len(rows) == 5
    assert sum(r.setting == "expression_min_range" for r in rows) == 2
    assert sum(r.setting == "pointing_min_total_ms" for r in rows) == 3


def test_evaluates_files_present_in_both_cache_and_labels_only() -> None:
    """Criterion 2."""
    cache = expression_cache() | {"extra": (no_face_frames(100), PLAN)}
    labels = {k: v for k, v in EXPRESSION_LABELS.items() if k != "a"}
    labels["ghost"] = (False, True)
    grid = {"expression_min_range": [0.20]}
    (row,) = evaluate(cache, labels, default_settings(), grid)
    assert sum(counts(row)) == 3  # b, c, d


def write_labels(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_read_labels_matches_by_stem_and_parses_booleans_ignoring_extra_columns(
    tmp_path: Path,
) -> None:
    """Criterion 1."""
    path = write_labels(
        tmp_path / "labels.csv",
        "file,is_finger_pointing,is_lack_expression,notes\n"
        "clips/a.webm,true,FALSE,hello\n"
        "b.webm,1,0,x\n"
        "c,Yes,No,y\n"
        "d.webm,False,TRUE,z\n",
    )
    assert read_labels(path) == {
        "a": (True, False),
        "b": (True, False),
        "c": (True, False),
        "d": (False, True),
    }


def make_row(setting: str, value: float, precision, recall, f1) -> Row:
    flag = "is_lack_expression" if setting == "expression_min_range" else "is_finger_pointing"
    return Row(flag, setting, value, 0, 0, 0, 0, precision, recall, f1)


def test_recommends_highest_recall_among_rows_meeting_the_precision_floor() -> None:
    """Criterion 1."""
    rows = [
        make_row("expression_min_range", 0.08, 0.95, 0.5, 0.65),
        make_row("expression_min_range", 0.12, 1.0, 0.8, 0.89),
        make_row("expression_min_range", 0.20, 0.7, 1.0, 0.82),
    ]
    assert recommend(rows, default_settings())["expression_min_range"].value == 0.12


def test_precision_exactly_at_the_floor_qualifies() -> None:
    """Criterion 1."""
    rows = [
        make_row("expression_min_range", 0.08, 0.9, 0.7, 0.8),
        make_row("expression_min_range", 0.10, 1.0, 0.4, 0.5),
    ]
    assert recommend(rows, default_settings())["expression_min_range"].value == 0.08


def test_breaks_recall_ties_by_f1_then_by_closeness_to_the_default() -> None:
    """Criterion 1."""
    by_f1 = [
        make_row("expression_min_range", 0.12, 1.0, 0.8, 0.89),
        make_row("expression_min_range", 0.16, 0.95, 0.8, 0.84),
    ]
    assert recommend(by_f1, default_settings())["expression_min_range"].value == 0.12
    by_default = [
        make_row("expression_min_range", 0.16, 1.0, 0.8, 0.85),
        make_row("expression_min_range", 0.10, 1.0, 0.8, 0.85),
    ]
    assert recommend(by_default, default_settings())["expression_min_range"].value == 0.10


def test_falls_back_to_highest_precision_when_none_meets_the_floor() -> None:
    """Criterion 1."""
    rows = [
        make_row("pointing_min_total_ms", 1000, 0.5, 1.0, 0.67),
        make_row("pointing_min_total_ms", 2000, 0.8, 0.6, 0.69),
        make_row("pointing_min_total_ms", 3000, None, 0.0, None),
    ]
    assert recommend(rows, default_settings())["pointing_min_total_ms"].value == 2000


def test_recommends_per_setting() -> None:
    """Criterion 1."""
    rows = [
        make_row("expression_min_range", 0.12, 1.0, 0.8, 0.89),
        make_row("pointing_min_total_ms", 2000, 0.8, 0.6, 0.69),
    ]
    assert set(recommend(rows, default_settings())) == {
        "expression_min_range",
        "pointing_min_total_ms",
    }


def run_main(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    labels_text: str,
    cache: dict,
    *extra: str,
) -> tuple[int, str]:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    for stem, (features, plan) in cache.items():
        save_cache(cache_dir / f"{stem}.npz", features, plan)
    labels = write_labels(tmp_path / "labels.csv", labels_text)
    code = calibrate.main([str(labels), "--cache", str(cache_dir), *extra])
    return code, capsys.readouterr().out


def csv_block(out: str) -> list[list[str]]:
    lines = out.splitlines()
    start = lines.index(",".join(HEADER))
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(next(csv.reader(io.StringIO(line))))
    return block


def test_main_prints_csv_header_and_formatted_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 1."""
    labels = "file,is_finger_pointing,is_lack_expression\n" + "".join(
        f"{stem}.webm,false,{str(lack).lower()}\n" for stem, (_, lack) in EXPRESSION_LABELS.items()
    )
    code, out = run_main(
        tmp_path, capsys, labels, expression_cache(), "--grid", "expression_min_range=0.04,0.2,0.0"
    )
    assert code == 0
    block = csv_block(out)
    assert block[0] == HEADER
    rows = {float(r[2]): r for r in block[1:] if r[1] == "expression_min_range"}
    assert rows[0.04][:2] == ["is_lack_expression", "expression_min_range"]
    assert rows[0.04][3:] == ["1", "0", "1", "2", "1.000", "0.333", "0.500"]
    assert rows[0.2][3:] == ["3", "1", "0", "0", "0.750", "1.000", "0.857"]
    assert rows[0.0][3:7] == ["0", "0", "1", "3"]
    assert rows[0.0][7] == "-"


def test_main_reports_missing_and_unlabeled_files_and_still_evaluates_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 2."""
    cache = {k: v for k, v in expression_cache().items() if k != "a"}
    cache["extra_clip"] = (block_step("smile", 0.15), PLAN)
    labels = (
        "file,is_finger_pointing,is_lack_expression\n"
        "b.webm,false,true\nc.webm,false,false\nd.webm,false,true\nmissing_clip.webm,false,true\n"
    )
    code, out = run_main(tmp_path, capsys, labels, cache, "--grid", "expression_min_range=0.20")
    assert code == 0
    assert out.index("missing from cache:") < out.index("missing_clip")
    assert out.index("missing_clip") < out.index("unlabeled in cache:")
    assert out.index("unlabeled in cache:") < out.index("extra_clip")
    rows = [r for r in csv_block(out)[1:] if r[1] == "expression_min_range"]
    assert len(rows) == 1
    assert [int(x) for x in rows[0][3:7]] == [2, 1, 0, 0]


def test_main_exits_2_when_no_file_is_in_both_cache_and_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 2."""
    labels = "file,is_finger_pointing,is_lack_expression\nnothing.webm,false,true\n"
    code, _ = run_main(tmp_path, capsys, labels, expression_cache())
    assert code == 2
