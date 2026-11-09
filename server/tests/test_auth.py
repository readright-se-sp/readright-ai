from conftest import valid_body


def assert_error_shape(response, code: str) -> None:
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


def test_missing_key_is_401(client):
    response = client.post("/v1/analyses", json=valid_body())
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "INVALID_REQUEST", "message": "Missing or invalid API key."}
    }


def test_wrong_key_is_401(client):
    response = client.post("/v1/analyses", json=valid_body(), headers={"X-API-Key": "nope"})
    assert response.status_code == 401
    assert_error_shape(response, "INVALID_REQUEST")


def test_empty_key_is_401(client):
    response = client.post("/v1/analyses", json=valid_body(), headers={"X-API-Key": ""})
    assert response.status_code == 401
    assert_error_shape(response, "INVALID_REQUEST")


def test_right_key_is_200(client, auth):
    assert client.post("/v1/analyses", json=valid_body(), headers=auth).status_code == 200


def test_auth_runs_before_body_parsing(client):
    response = client.post(
        "/v1/analyses", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401


def test_health_needs_no_key(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_openapi_declares_api_key_scheme(client):
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert schemes["ApiKeyAuth"]["name"] == "X-API-Key"
