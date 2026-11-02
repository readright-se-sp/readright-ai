"""Calibration tolerates bad inputs: no pose data, unreadable cache, bad labels, bad grid values."""

from pathlib import Path

import numpy as np
import pytest
from devtool_helpers import default_settings
from expression_helpers import block_step
from pointing_helpers import hands_in_range, make_features, resting_pose

from app.frames import SamplePlan
from devtools import calibrate
from devtools.calibrate import Row, read_labels, recommend
from devtools.feature_cache import save_cache

PLAN = SamplePlan(fps=5.0, width=640, height=480, max_frames=900)
LABEL_HEADER = "file,is_finger_pointing,is_lack_expression\n"
NO_POSE_NOTE = "note: cache has no pose data; use_pose skipped"


def populate(cache_dir: Path, cache: dict) -> Path:
    cache_dir.mkdir(exist_ok=True)
    for stem, (features, plan) in cache.items():
        save_cache(cache_dir / f"{stem}.npz", features, plan)
    return cache_dir


def run_main(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    labels_text: str,
    cache: dict,
    *extra: str,
) -> tuple[int, str]:
    cache_dir = populate(tmp_path / "cache", cache)
    labels = tmp_path / "labels.csv"
    labels.write_text(labels_text, encoding="utf-8")
    code = calibrate.main([str(labels), "--cache", str(cache_dir), *extra])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def settings_in(out: str) -> set[str]:
    return {line.split(",")[1] for line in out.splitlines() if line.startswith("is_")}


def pointing_cache(with_pose: bool) -> dict:
    pose_at = (lambda i: resting_pose()) if with_pose else None
    return {
        "p1": (make_features(100, hands_in_range(10, 30), pose_at), PLAN),
        "p2": (make_features(100, None, pose_at), PLAN),
    }


POINTING_LABELS = LABEL_HEADER + "p1.webm,true,false\np2.webm,false,false\n"


def test_skips_use_pose_and_prints_a_note_when_no_file_has_pose_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 9."""
    code, out = run_main(tmp_path, capsys, POINTING_LABELS, pointing_cache(with_pose=False))
    assert code == 0
    assert NO_POSE_NOTE in out
    assert "use_pose" not in settings_in(out)
    assert "pointing_min_total_ms" in settings_in(out)


def test_keeps_use_pose_in_the_grid_when_a_file_has_pose_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 9."""
    code, out = run_main(tmp_path, capsys, POINTING_LABELS, pointing_cache(with_pose=True))
    assert code == 0
    assert NO_POSE_NOTE not in out
    assert "use_pose" in settings_in(out)


def good_cache() -> dict:
    return {
        "b": (block_step("smile", 0.08), PLAN),
        "c": (block_step("smile", 0.15), PLAN),
    }


EXPRESSION_LABELS = LABEL_HEADER + "".join(
    f"{stem}.webm,false,{lack}\n" for stem, lack in (("b", "true"), ("c", "false"), ("bad", "true"))
)


def test_lists_a_corrupt_cache_file_as_unreadable_and_evaluates_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 10."""
    populate(tmp_path / "cache", good_cache())
    (tmp_path / "cache" / "bad.npz").write_bytes(b"this is not an npz archive")
    code, out = run_main(
        tmp_path, capsys, EXPRESSION_LABELS, {}, "--grid", "expression_min_range=0.10"
    )
    assert code == 0
    assert out.index("unreadable cache:") < out.index("bad", out.index("unreadable cache:"))
    rows = [line.split(",") for line in out.splitlines() if line.startswith("is_lack_expression")]
    assert len(rows) == 1
    assert [int(x) for x in rows[0][3:7]] == [1, 0, 1, 0]


def test_lists_a_cache_file_with_the_wrong_version_as_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 10."""
    cache_dir = populate(tmp_path / "cache", good_cache())
    np.savez_compressed(cache_dir / "bad.npz", version=np.int64(999))
    code, out = run_main(
        tmp_path, capsys, EXPRESSION_LABELS, {}, "--grid", "expression_min_range=0.10"
    )
    assert code == 0
    assert out.index("unreadable cache:") < out.index("bad", out.index("unreadable cache:"))


@pytest.mark.parametrize(
    ("text", "row_number"),
    [
        (LABEL_HEADER + "a.webm,true,false\nb.webm,false,true\nc.webm,maybe,false\n", "3"),
        (LABEL_HEADER + "a.webm,true,false\nb.webm,false\n", "2"),
        (LABEL_HEADER + "a.webm,true,false\nb.webm,false,true\nc.webm,true,\n", "3"),
    ],
)
def test_read_labels_names_the_data_row_with_a_missing_or_invalid_cell(
    tmp_path: Path, text: str, row_number: str
) -> None:
    """Criterion 11."""
    path = tmp_path / "labels.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=rf"\b{row_number}\b"):
        read_labels(path)


def test_read_labels_names_a_duplicate_stem(tmp_path: Path) -> None:
    """Criterion 11."""
    path = tmp_path / "labels.csv"
    path.write_text(
        LABEL_HEADER + "dupe_clip.webm,true,false\nclips/dupe_clip.webm,false,true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="dupe_clip"):
        read_labels(path)


def test_main_prints_a_label_error_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 11."""
    labels = LABEL_HEADER + "b.webm,false,true\nc.webm,perhaps,false\n"
    code, out = run_main(tmp_path, capsys, labels, good_cache())
    assert code == 2
    assert "2" in out


def make_row(setting: str, value: float, precision, recall, f1) -> Row:
    return Row("is_lack_expression", setting, value, 0, 0, 0, 0, precision, recall, f1)


def test_recommend_breaks_ties_by_closeness_to_the_given_base() -> None:
    """Criterion 12."""
    rows = [
        make_row("expression_min_range", 0.08, 1.0, 0.8, 0.85),
        make_row("expression_min_range", 0.20, 1.0, 0.8, 0.85),
    ]
    near_low = default_settings(expression_min_range=0.06)
    near_high = default_settings(expression_min_range=0.18)
    assert recommend(rows, near_low)["expression_min_range"].value == 0.08
    assert recommend(rows, near_high)["expression_min_range"].value == 0.20


def test_recommend_fallback_ignores_rows_with_undefined_precision() -> None:
    """Criterion 12."""
    rows = [
        make_row("expression_min_range", 0.04, None, 0.0, None),
        make_row("expression_min_range", 0.10, 0.4, 0.5, 0.44),
        make_row("expression_min_range", 0.20, 0.6, 0.9, 0.72),
    ]
    assert recommend(rows, default_settings())["expression_min_range"].value == 0.20


def test_recommend_gives_no_recommendation_when_every_precision_is_undefined() -> None:
    """Criterion 12."""
    rows = [
        make_row("expression_min_range", 0.04, None, 0.0, None),
        make_row("expression_min_range", 0.10, None, 0.0, None),
    ]
    assert recommend(rows, default_settings()).get("expression_min_range") is None


def test_report_says_no_recommendation_for_a_setting_without_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 12."""
    code, out = run_main(
        tmp_path,
        capsys,
        POINTING_LABELS,
        pointing_cache(with_pose=False),
        "--grid",
        "pointing_min_total_ms=60000",
    )
    assert code == 0
    assert "no recommendation" in out


@pytest.mark.parametrize(
    ("grid", "setting", "value"),
    [
        ("expression_min_range=2", "expression_min_range", "2"),
        ("sweep_min_count=0", "sweep_min_count", "0"),
        ("pointing_min_total_ms=0.1,-5", "pointing_min_total_ms", "-5"),
    ],
)
def test_main_rejects_grid_values_that_break_settings_constraints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], grid: str, setting: str, value: str
) -> None:
    """Criterion 13."""
    labels = LABEL_HEADER + "b.webm,false,true\nc.webm,false,false\n"
    code, out = run_main(tmp_path, capsys, labels, good_cache(), "--grid", grid)
    assert code == 2
    assert setting in out
    assert value in out
