"""Frame inspection prints CSV rows to stdout and the decision to stderr."""

import csv
import io
import json
from pathlib import Path

import pytest
from devtool_helpers import FRAME_COUNT, fake_session_factory, make_webm, patch_video_io

from app.expression import EXPRESSION_GROUPS
from devtools import inspect_video

COLUMNS = [
    "index",
    "timestamp_ms",
    "face",
    *EXPRESSION_GROUPS,
    "hands",
    "pointing_hand",
    "arm_toward_screen",
    "tip_x",
]
TRUE_TEXT = {"1", "true", "yes"}


@pytest.fixture
def webm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    patch_video_io(monkeypatch, inspect_video)
    return make_webm(tmp_path, "clip.webm")


def run(webm: Path, capsys: pytest.CaptureFixture[str], *flags: str) -> tuple[int, str, str]:
    code = inspect_video.main([str(webm), *flags], session_factory=fake_session_factory())
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def parse_rows(out: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(out)))


def test_stdout_is_only_csv_with_header_and_one_row_per_frame(
    webm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 4."""
    code, out, _ = run(webm, capsys)
    assert code == 0
    parsed = list(csv.reader(io.StringIO(out)))
    assert parsed[0] == COLUMNS
    assert len(parsed) == 1 + FRAME_COUNT
    assert all(len(line) == len(COLUMNS) for line in parsed)
    rows = parse_rows(out)
    assert [r["index"] for r in rows] == [str(i) for i in range(FRAME_COUNT)]
    assert [r["timestamp_ms"] for r in rows] == [str(i * 200) for i in range(FRAME_COUNT)]


def test_decision_goes_to_stderr_and_not_stdout(
    webm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 4."""
    _, out, err = run(webm, capsys)
    assert "flags" not in out
    assert "{" not in out
    decision = json.loads(err[err.index("{") :])
    assert set(decision["flags"]) == {"is_finger_pointing", "is_lack_expression"}
    assert "sweeps" in err


def test_row_values_follow_the_frame_features(
    webm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 4."""
    _, out, _ = run(webm, capsys)
    rows = parse_rows(out)
    no_face = rows[0]
    assert all(no_face[group] == "" for group in EXPRESSION_GROUPS)
    assert no_face["hands"] == "0"
    assert no_face["tip_x"] == ""
    assert float(rows[1]["smile"]) == pytest.approx(0.35)  # mean of the two smile members
    assert rows[1]["brow_raise"] == ""
    assert rows[2]["hands"] == "1"
    assert float(rows[2]["tip_x"]) == pytest.approx(0.44)
    assert rows[3]["hands"] == "2"
    assert rows[2]["pointing_hand"].lower() in TRUE_TEXT
    assert rows[0]["pointing_hand"].lower() not in TRUE_TEXT
    assert rows[4]["pointing_hand"].lower() not in TRUE_TEXT


def test_arm_toward_screen_is_reported_only_with_pose(
    webm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 4."""
    _, out, _ = run(webm, capsys)
    assert not any(r["arm_toward_screen"].lower() in TRUE_TEXT for r in parse_rows(out))
    _, out, err = run(webm, capsys, "--pose")
    rows = parse_rows(out)
    assert rows[2]["arm_toward_screen"].lower() in TRUE_TEXT
    assert rows[5]["arm_toward_screen"].lower() in TRUE_TEXT
    assert rows[0]["arm_toward_screen"].lower() not in TRUE_TEXT
    assert "{" in err
