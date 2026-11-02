import os

import pytest
from video_helpers import FakeAnalyzer, make_client, make_settings, valid_body


def assert_invalid_job(response) -> None:
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_JOB"
    assert isinstance(body["error"]["message"], str)


def test_rejects_nul_byte_path_when_media_dir_is_set(tmp_path):
    """Criterion 19."""
    media = tmp_path / "media"
    media.mkdir()
    analyzer = FakeAnalyzer()
    path = f"{media}/bad\x00name.webm"
    with make_client(make_settings(allowed_media_dir=media), analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(path))
    assert_invalid_job(response)
    assert analyzer.calls == []


def test_rejects_nul_byte_path_without_media_dir(settings, tmp_path):
    """Criterion 19."""
    analyzer = FakeAnalyzer()
    path = f"{tmp_path}/bad\x00name.webm"
    with make_client(settings, analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(path))
    assert_invalid_job(response)
    assert analyzer.calls == []


def test_rejects_symlink_loop_when_media_dir_is_set(tmp_path):
    """Criterion 19."""
    media = tmp_path / "media"
    media.mkdir()
    loop_a = media / "a"
    loop_b = media / "b"
    try:
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted in this environment")
    analyzer = FakeAnalyzer()
    with make_client(make_settings(allowed_media_dir=media), analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(loop_a / "x.webm"))
    assert_invalid_job(response)
    assert analyzer.calls == []
