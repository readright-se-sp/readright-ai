import time

from fastapi.testclient import TestClient
from video_helpers import FakeAnalyzer, make_client, make_settings, valid_body


def error_code(response) -> str:
    body = response.json()
    assert set(body) == {"error"}
    assert isinstance(body["error"]["message"], str)
    return body["error"]["code"]


def test_rejects_relative_webm_path(client: TestClient, analyzer: FakeAnalyzer):
    """Criterion 4."""
    response = client.post("/v1/jobs", json=valid_body("relative/recording.webm"))
    assert response.status_code == 400
    assert error_code(response) == "INVALID_JOB"
    assert analyzer.calls == []


def test_rejects_bare_filename_webm_path(client: TestClient, analyzer: FakeAnalyzer):
    """Criterion 4."""
    response = client.post("/v1/jobs", json=valid_body("recording.webm"))
    assert response.status_code == 400
    assert error_code(response) == "INVALID_JOB"
    assert analyzer.calls == []


def test_rejects_path_outside_allowed_media_dir(tmp_path):
    """Criterion 4."""
    media = tmp_path / "media"
    media.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "x.webm"
    target.write_bytes(b"")
    analyzer = FakeAnalyzer()
    with make_client(make_settings(allowed_media_dir=media), analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(target))
    assert response.status_code == 400
    assert error_code(response) == "INVALID_JOB"
    assert analyzer.calls == []


def test_rejects_dotdot_escape_from_allowed_media_dir(tmp_path):
    """Criterion 4."""
    media = tmp_path / "media"
    (media / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.webm").write_bytes(b"")
    escaping = f"{media}/sub/../../outside/x.webm"
    analyzer = FakeAnalyzer()
    with make_client(make_settings(allowed_media_dir=media), analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(escaping))
    assert response.status_code == 400
    assert error_code(response) == "INVALID_JOB"
    assert analyzer.calls == []


def test_accepts_path_inside_allowed_media_dir(tmp_path):
    """Criterion 4."""
    media = tmp_path / "media"
    media.mkdir()
    target = media / "ok.webm"
    target.write_bytes(b"")
    analyzer = FakeAnalyzer()
    with make_client(make_settings(allowed_media_dir=media), analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(target))
    assert response.status_code == 200
    assert len(analyzer.calls) == 1


def test_rejects_missing_file_as_unreadable(client: TestClient, tmp_path, analyzer: FakeAnalyzer):
    """Criterion 5."""
    response = client.post("/v1/jobs", json=valid_body(tmp_path / "missing.webm"))
    assert response.status_code == 422
    assert error_code(response) == "MEDIA_UNREADABLE"
    assert analyzer.calls == []


def test_rejects_directory_as_unreadable(client: TestClient, tmp_path, analyzer: FakeAnalyzer):
    """Criterion 5."""
    response = client.post("/v1/jobs", json=valid_body(tmp_path))
    assert response.status_code == 422
    assert error_code(response) == "MEDIA_UNREADABLE"
    assert analyzer.calls == []


def test_rejects_expired_deadline(client: TestClient, webm_file, analyzer: FakeAnalyzer):
    """Criterion 6."""
    deadline = int(time.time() * 1000) - 1000
    response = client.post("/v1/jobs", json=valid_body(webm_file, deadline=deadline))
    assert response.status_code == 504
    assert error_code(response) == "DEADLINE_EXCEEDED"
    assert analyzer.calls == []


def test_rejects_zero_deadline(client: TestClient, webm_file, analyzer: FakeAnalyzer):
    """Criterion 6."""
    response = client.post("/v1/jobs", json=valid_body(webm_file, deadline=0))
    assert response.status_code == 504
    assert error_code(response) == "DEADLINE_EXCEEDED"
    assert analyzer.calls == []


def test_checks_deadline_before_file_existence(client: TestClient, tmp_path, analyzer):
    """Criterion 6."""
    body = valid_body(tmp_path / "missing.webm", deadline=1)
    response = client.post("/v1/jobs", json=body)
    assert response.status_code == 504
    assert error_code(response) == "DEADLINE_EXCEEDED"
    assert analyzer.calls == []


def test_checks_deadline_before_path_rules(client: TestClient, analyzer: FakeAnalyzer):
    """Criterion 6."""
    response = client.post("/v1/jobs", json=valid_body("relative.webm", deadline=1))
    assert response.status_code == 504
    assert error_code(response) == "DEADLINE_EXCEEDED"
    assert analyzer.calls == []
