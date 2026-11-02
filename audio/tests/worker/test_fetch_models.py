import json
from pathlib import Path

import pytest

from devtools import fetch_models


def test_lock_file_is_pinned() -> None:
    lock = fetch_models.read_lock()
    assert set(lock) == {"crisperwhisper", "phoneme", "align"}
    for name in ("crisperwhisper", "phoneme"):
        assert len(lock[name].revision) == 40


def test_branch_name_revision_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "models.lock"
    path.write_text("crisperwhisper hf nyralabs/x main\n", encoding="utf-8")
    with pytest.raises(ValueError, match="40-character"):
        fetch_models.read_lock(path)


def write_model(path: Path, tokens: list[str]) -> None:
    path.mkdir()
    (path / "vocabulary.json").write_text(json.dumps(["a", *tokens]), encoding="utf-8")
    (path / "tokenizer.json").write_text(json.dumps({"added": tokens}), encoding="utf-8")


def test_verbatim_tokens_present(tmp_path: Path) -> None:
    write_model(tmp_path / "ok", list(fetch_models.VERBATIM_TOKENS))
    fetch_models.check_verbatim_tokens(tmp_path / "ok")


def test_missing_verbatim_tokens_fail_the_build(tmp_path: Path) -> None:
    write_model(tmp_path / "bad", list(fetch_models.VERBATIM_TOKENS[:3]))
    with pytest.raises(RuntimeError, match="verbatim"):
        fetch_models.check_verbatim_tokens(tmp_path / "bad")
