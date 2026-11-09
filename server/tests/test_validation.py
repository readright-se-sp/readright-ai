import pytest
from conftest import SIGNATURE, valid_body

REQUIRED = ["request_id", "video_url", "passage_title", "passage_content"]


def post_json(client, auth, payload):
    return client.post("/v1/analyses", json=payload, headers=auth)


def assert_invalid(response) -> None:
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert "details" in body["error"]
    assert "input" not in response.text
    assert SIGNATURE not in response.text


@pytest.mark.parametrize("field", REQUIRED)
def test_missing_required_field(client, auth, field):
    payload = valid_body()
    del payload[field]
    response = post_json(client, auth, payload)
    assert_invalid(response)
    assert any(field in e["loc"] for e in response.json()["error"]["details"]["errors"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_id": "not-a-uuid"},
        {"video_url": "not a url"},
        {"passage_title": ""},
        {"passage_title": "   \n\t"},
        {"passage_content": ""},
        {"passage_content": "   "},
    ],
)
def test_invalid_values(client, auth, overrides):
    assert_invalid(post_json(client, auth, valid_body(**overrides)))


def test_bad_url_does_not_echo_input(client, auth):
    response = post_json(client, auth, valid_body(video_url=f"ftp://x/y?sig={SIGNATURE}"))
    assert_invalid(response)


def test_malformed_json(client, auth):
    response = client.post(
        "/v1/analyses",
        content=b"{not json",
        headers={**auth, "Content-Type": "application/json"},
    )
    assert_invalid(response)


def test_json_array_instead_of_object(client, auth):
    assert_invalid(post_json(client, auth, [valid_body()]))


def test_unknown_field_is_ignored(client, auth):
    assert post_json(client, auth, valid_body(extra_field="x")).status_code == 200
