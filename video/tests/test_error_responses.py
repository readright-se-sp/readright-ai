import pytest
from fastapi.testclient import TestClient
from video_helpers import FakeAnalyzer, make_client, valid_body

from app.errors import STATUS_BY_CODE, LaneError
from app.models import ErrorCode


def assert_error_shape(response, status: int, code: str | None = None) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert isinstance(body["error"]["message"], str)
    if code is not None:
        assert body["error"]["code"] == code
    return body


def test_maps_media_unreadable_from_analyzer_to_422(settings, webm_file):
    """Criterion 7."""
    analyzer = FakeAnalyzer(analyze_error=LaneError(ErrorCode.MEDIA_UNREADABLE, "bad video"))
    with make_client(settings, analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(webm_file))
    assert_error_shape(response, 422, "MEDIA_UNREADABLE")


def test_maps_deadline_exceeded_from_analyzer_to_504(settings, webm_file):
    """Criterion 7."""
    analyzer = FakeAnalyzer(analyze_error=LaneError(ErrorCode.DEADLINE_EXCEEDED, "too slow"))
    with make_client(settings, analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(webm_file))
    assert_error_shape(response, 504, "DEADLINE_EXCEEDED")


def test_hides_unexpected_exception_text_in_500(settings, webm_file):
    """Criterion 7."""
    analyzer = FakeAnalyzer(analyze_error=RuntimeError("sensitive-detail-123"))
    with make_client(settings, analyzer) as client:
        response = client.post("/v1/jobs", json=valid_body(webm_file))
    assert_error_shape(response, 500, "INTERNAL")
    assert "sensitive-detail-123" not in response.text


def test_status_table_matches_contract():
    """Criterion 7."""
    assert STATUS_BY_CODE[ErrorCode.INVALID_JOB] == 400
    assert STATUS_BY_CODE[ErrorCode.MEDIA_UNREADABLE] == 422
    assert STATUS_BY_CODE[ErrorCode.DEADLINE_EXCEEDED] == 504
    assert STATUS_BY_CODE[ErrorCode.INTERNAL] == 500


def test_lane_error_carries_code_message_and_status():
    """Criterion 7."""
    error = LaneError(ErrorCode.MEDIA_UNREADABLE, "nope")
    assert error.code == ErrorCode.MEDIA_UNREADABLE
    assert error.message == "nope"
    assert error.status == 422


def test_unknown_route_returns_error_body_404(client: TestClient):
    """Criterion 8."""
    assert_error_shape(client.get("/no/such/route"), 404)


@pytest.mark.parametrize(("method", "path"), [("get", "/v1/jobs"), ("post", "/health/ready")])
def test_wrong_method_returns_error_body_405(client: TestClient, method, path):
    """Criterion 8."""
    assert_error_shape(getattr(client, method)(path), 405)
