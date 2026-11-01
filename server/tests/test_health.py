import fakeredis.aioredis
from fastapi.testclient import TestClient

from app.main import create_app


def test_live(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"redis": True}}


def test_ready_when_redis_is_down(settings, redis_server):
    redis_server.connected = False
    app = create_app(settings, redis=fakeredis.aioredis.FakeRedis(server=redis_server))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    error = response.json()["error"]
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert error["details"] == {"failed_checks": ["redis"]}


def test_ready_with_failing_check(app, client):
    async def ok() -> bool:
        return True

    async def failing() -> bool:
        return False

    app.state.readiness_checks = {"ok": ok, "redis": failing}
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    error = response.json()["error"]
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert error["details"] == {"failed_checks": ["redis"]}


def test_ready_with_raising_check(app, client):
    async def boom() -> bool:
        raise ConnectionError("down")

    app.state.readiness_checks = {"s3": boom}
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "down" not in response.text
