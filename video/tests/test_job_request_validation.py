import pytest
from fastapi.testclient import TestClient
from video_helpers import FakeAnalyzer, valid_body

SECRET_ID = "SECRET-JOB-ID-xyz"
SECRET_PATH = "/secret/dir/SECRET-PATH.webm"


def assert_invalid_job(response, *forbidden: str) -> None:
    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "INVALID_JOB"
    assert isinstance(body["error"]["message"], str)
    for text in forbidden:
        assert text not in response.text


def test_returns_false_flags_for_valid_job(client: TestClient, webm_file, analyzer: FakeAnalyzer):
    """Criterion 1."""
    response = client.post("/v1/jobs", json=valid_body(webm_file))
    assert response.status_code == 200
    assert response.json() == {
        "job_id": "job-1",
        "is_finger_pointing": False,
        "is_lack_expression": False,
    }
    assert len(analyzer.calls) == 1


def test_echoes_job_id_exactly_with_case(client: TestClient, webm_file):
    """Criterion 2."""
    response = client.post("/v1/jobs", json=valid_body(webm_file, job_id="AbC-123_xYz"))
    assert response.status_code == 200
    assert response.json()["job_id"] == "AbC-123_xYz"


@pytest.mark.parametrize("missing", ["job_id", "deadline", "webm_path"])
def test_rejects_missing_field(client: TestClient, webm_file, analyzer, missing):
    """Criterion 3."""
    body = valid_body(webm_file, job_id=SECRET_ID)
    del body[missing]
    response = client.post("/v1/jobs", json=body)
    assert_invalid_job(response, SECRET_ID, str(webm_file))
    assert analyzer.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_id": 123},
        {"job_id": ""},
        {"deadline": "123"},
        {"deadline": 1.5},
        {"deadline": True},
        {"webm_path": 5},
        {"webm_path": None},
    ],
)
def test_rejects_wrong_field_types(client: TestClient, analyzer, overrides):
    """Criterion 3."""
    body = {"job_id": SECRET_ID, "deadline": 4102444800000, "webm_path": SECRET_PATH}
    body.update(overrides)
    response = client.post("/v1/jobs", json=body)
    assert_invalid_job(response)
    if "webm_path" not in overrides:
        assert SECRET_PATH not in response.text
    if "job_id" not in overrides:
        assert SECRET_ID not in response.text
    assert analyzer.calls == []


def test_rejects_non_json_body(client: TestClient, analyzer):
    """Criterion 3."""
    response = client.post(
        "/v1/jobs", content=b"this is not json", headers={"Content-Type": "application/json"}
    )
    assert_invalid_job(response)
    assert analyzer.calls == []


@pytest.mark.parametrize("payload", [[1, 2, 3], "just a string", 42, None])
def test_rejects_non_object_body(client: TestClient, analyzer, payload):
    """Criterion 3."""
    response = client.post("/v1/jobs", json=payload)
    assert_invalid_job(response)
    assert analyzer.calls == []


def test_ignores_unknown_extra_fields(client: TestClient, webm_file):
    """Criterion 3."""
    body = valid_body(webm_file, extra="ignored", nested={"a": 1})
    response = client.post("/v1/jobs", json=body)
    assert response.status_code == 200
    assert set(response.json()) == {"job_id", "is_finger_pointing", "is_lack_expression"}
