import pytest
from fastapi.testclient import TestClient

from app.errors import STATUS_BY_CODE, MAIError
from app.models import ErrorCode


@pytest.fixture
def error_client(app):
    @app.get("/test/raise/{code}")
    async def raise_code(code: ErrorCode):
        raise MAIError(code, "boom", details={"k": "v"})

    @app.get("/test/crash")
    async def crash():
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_every_code_has_a_status():
    assert set(STATUS_BY_CODE) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_code_maps_to_status(error_client, code):
    response = error_client.get(f"/test/raise/{code.value}")
    assert response.status_code == STATUS_BY_CODE[code]
    assert response.json() == {
        "error": {"code": code.value, "message": "boom", "details": {"k": "v"}}
    }


@pytest.mark.parametrize("code", [ErrorCode.RATE_LIMITED, ErrorCode.SERVICE_UNAVAILABLE])
def test_retry_after_present(error_client, code):
    assert error_client.get(f"/test/raise/{code.value}").headers["Retry-After"] == "5"


def test_no_retry_after_on_other_codes(error_client):
    assert "Retry-After" not in error_client.get("/test/raise/TIMEOUT").headers


def test_unhandled_exception_is_500_without_traceback(error_client):
    response = error_client.get("/test/crash")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text


def test_unknown_path_is_404_in_error_shape(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_wrong_method_is_405_in_error_shape(client):
    response = client.post("/health/live")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
