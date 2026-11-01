"""Phase 3: presigned-URL download (criteria 8-14). respx only, no real network."""

import logging
from pathlib import Path

import httpx
import pytest
import respx

from app.deadline import Deadline
from app.download import download_video
from app.errors import MAIError
from app.models import ErrorCode

pytestmark = pytest.mark.anyio

SECRET = "SUPERSECRETSIGNATURE123"
BASE = "https://bucket.s3.ap-southeast-1.amazonaws.com/recordings/abc.webm"
URL = f"{BASE}?X-Amz-Signature={SECRET}&X-Amz-Date=20260101T000000Z"
MAX = 1000


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "input.webm"


async def fetch(dest: Path, *, max_bytes: int = MAX, dl: Deadline | None = None) -> int:
    return await download_video(URL, dest, max_bytes=max_bytes, deadline=dl or Deadline(100.0))


def route(mock):
    return mock.get(url__regex=r"https://bucket\.s3\.ap-southeast-1\.amazonaws\.com/.*")


# 8. client configuration ------------------------------------------------------------------------


class _Recorder(httpx.AsyncClient):
    instances: list["_Recorder"] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.call_kwargs: dict = {}
        super().__init__(*args, **kwargs)
        _Recorder.instances.append(self)

    def stream(self, method, url, **kwargs):
        self.call_kwargs.update(kwargs)
        return super().stream(method, url, **kwargs)

    async def send(self, request, **kwargs):
        self.call_kwargs.update(kwargs)
        return await super().send(request, **kwargs)


def _effective_timeout(client: _Recorder) -> httpx.Timeout:
    value = client.call_kwargs.get("timeout", httpx.USE_CLIENT_DEFAULT)
    if value is httpx.USE_CLIENT_DEFAULT or value is None:
        value = client.init_kwargs.get("timeout")
    return value if isinstance(value, httpx.Timeout) else httpx.Timeout(value)


async def test_c8_returns_bytes_written_and_does_not_follow_redirects(mock, dest, monkeypatch):
    _Recorder.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", _Recorder)
    route(mock).respond(200, content=b"x" * 10)
    assert await fetch(dest) == 10
    assert len(_Recorder.instances) == 1
    assert _Recorder.instances[0].init_kwargs.get("follow_redirects", False) is False


async def test_c8_timeouts_are_capped_by_remaining_deadline(mock, dest, monkeypatch):
    _Recorder.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", _Recorder)
    route(mock).respond(200, content=b"x")
    # Plenty of time: the fixed caps apply.
    await fetch(dest, dl=Deadline(100.0))
    big = _effective_timeout(_Recorder.instances[-1])
    assert (big.connect, big.read, big.write, big.pool) == (10, 30, 30, 30)
    # Little time left: everything is capped to what remains.
    await fetch(dest, dl=Deadline(2.0, clock=lambda: 0.0))
    small = _effective_timeout(_Recorder.instances[-1])
    for value in (small.connect, small.read, small.write, small.pool):
        assert value == pytest.approx(2.0)


async def test_c8_redirect_is_not_followed(mock, dest):
    route(mock).respond(302, headers={"Location": "https://evil.example/x"})
    other = mock.get("https://evil.example/x").respond(200, content=b"nope")
    with pytest.raises(MAIError) as exc:
        await fetch(dest)
    assert exc.value.code == ErrorCode.VIDEO_DOWNLOAD_FAILED
    assert not other.called


# 9. success -------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 206])
async def test_c9_any_2xx_writes_body_to_dest(mock, dest, status):
    body = bytes(range(256)) * 3
    route(mock).respond(status, content=body)
    assert await fetch(dest) == len(body)
    assert dest.read_bytes() == body


# 10. expired ------------------------------------------------------------------------------------


async def test_c10_expired_403_maps_to_video_url_expired(mock, dest):
    body = "<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>"
    route(mock).respond(403, content=body.encode())
    with pytest.raises(MAIError) as exc:
        await fetch(dest)
    assert exc.value.code == ErrorCode.VIDEO_URL_EXPIRED
    assert exc.value.status == 422


# 11. other non-2xx ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>"),
        (403, b""),
        (404, b"not found"),
        (301, b""),
        (302, b""),
        (500, b"oops"),
        (503, b"busy"),
    ],
)
async def test_c11_other_non_2xx_is_download_failed_with_upstream_status(mock, dest, status, body):
    route(mock).respond(status, content=body)
    with pytest.raises(MAIError) as exc:
        await fetch(dest)
    assert exc.value.code == ErrorCode.VIDEO_DOWNLOAD_FAILED
    assert exc.value.status == 422
    assert exc.value.details == {"upstream_status": status}


# 12. network errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")],
    ids=["connect", "read_timeout", "protocol"],
)
async def test_c12_network_error_is_download_failed(mock, dest, error):
    route(mock).mock(side_effect=error)
    with pytest.raises(MAIError) as exc:
        await fetch(dest)
    assert exc.value.code == ErrorCode.VIDEO_DOWNLOAD_FAILED
    assert exc.value.status == 422


async def test_c12_network_error_after_deadline_is_timeout(mock, dest):
    now = [0.0]

    def boom(request):
        now[0] = 99.0  # the deadline passes while the request is in flight
        raise httpx.ReadTimeout("slow")

    route(mock).mock(side_effect=boom)
    with pytest.raises(MAIError) as exc:
        await fetch(dest, dl=Deadline(5.0, clock=lambda: now[0]))
    assert exc.value.code == ErrorCode.TIMEOUT
    assert exc.value.status == 504


# 13. size limit ---------------------------------------------------------------------------------


async def test_c13_content_length_over_max_fails_before_reading(mock, dest):
    route(mock).respond(200, content=b"x" * 10, headers={"Content-Length": str(MAX + 1)})
    with pytest.raises(MAIError) as exc:
        await fetch(dest)
    assert exc.value.code == ErrorCode.VIDEO_DOWNLOAD_FAILED
    assert exc.value.details == {"max_bytes": MAX}
    assert not dest.exists()


async def test_c13_streamed_bytes_over_max_without_content_length(mock, dest):
    async def chunks():
        for _ in range(10):
            yield b"x" * 200

    route(mock).respond(200, content=chunks())
    with pytest.raises(MAIError) as exc:
        await fetch(dest, max_bytes=500)
    assert exc.value.code == ErrorCode.VIDEO_DOWNLOAD_FAILED
    assert exc.value.details == {"max_bytes": 500}
    assert not dest.exists()


async def test_c13_streamed_bytes_over_max_with_lying_content_length(mock, dest):
    route(mock).respond(200, content=b"x" * 2000, headers={"Content-Length": "5"})
    with pytest.raises(MAIError) as exc:
        await fetch(dest, max_bytes=500)
    assert exc.value.details == {"max_bytes": 500}
    assert not dest.exists()


async def test_c13_exactly_max_bytes_is_allowed(mock, dest):
    route(mock).respond(200, content=b"x" * 500)
    assert await fetch(dest, max_bytes=500) == 500


# 14. no query string anywhere -------------------------------------------------------------------


@pytest.mark.parametrize("setup", ["expired", "forbidden", "not_found", "network", "too_big", "ok"])
async def test_c14_query_string_never_leaks(mock, dest, caplog, setup):
    caplog.set_level(logging.DEBUG)
    r = route(mock)
    if setup == "expired":
        r.respond(403, content=b"Request has expired")
    elif setup == "forbidden":
        r.respond(403, content=b"nope")
    elif setup == "not_found":
        r.respond(404)
    elif setup == "network":
        r.mock(side_effect=httpx.ConnectError("refused"))
    elif setup == "too_big":
        r.respond(200, content=b"x" * 2000)
    else:
        r.respond(200, content=b"x")

    texts: list[str] = []
    try:
        await fetch(dest, max_bytes=1500)
    except MAIError as err:
        texts += [str(err), err.message, repr(err.details)]

    # Logging for the httpx library itself is configured by the app (the pipeline tests run
    # through create_app and check every record); here we check the download module's own.
    own = [r for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))]
    texts += [r.getMessage() for r in own] + [str(r.__dict__) for r in own]
    joined = "\n".join(texts)
    assert SECRET not in joined
    assert "X-Amz-Signature" not in joined
    assert "X-Amz-Date" not in joined
