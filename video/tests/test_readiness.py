import threading

from video_helpers import FakeAnalyzer, make_client, wait_until

NOT_READY_BODY = {"error": {"code": "INTERNAL", "message": "Models are not loaded."}}


def test_reports_ready_when_analyzer_is_ready(client):
    """Criterion 9."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_startup_does_not_wait_for_load_and_becomes_ready_after(settings):
    """Criterion 9."""
    gate = threading.Event()
    analyzer = FakeAnalyzer(ready=False, load_gate=gate)
    try:
        with make_client(settings, analyzer) as client:
            # Entering the context returned although load() is still blocked.
            assert not analyzer.load_finished.is_set()
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == NOT_READY_BODY

            gate.set()
            assert wait_until(lambda: client.get("/health/ready").status_code == 200)
            assert client.get("/health/ready").json() == {"status": "ready"}
    finally:
        gate.set()


def test_keeps_serving_when_load_fails(settings, log_records):
    """Criterion 9."""
    analyzer = FakeAnalyzer(ready=False, load_error=RuntimeError("model file corrupt"))
    with make_client(settings, analyzer) as client:
        assert analyzer.load_finished.wait(2)
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == NOT_READY_BODY
        stats = client.get("/stats")
        assert stats.status_code == 200
        assert wait_until(lambda: any(r.levelno >= 40 for r in log_records.records))
