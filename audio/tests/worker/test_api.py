import threading
import time
import wave
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from audio.config import AudioSettings
from audio.errors import NoSpeech
from audio.models import AudioResult, ReadingScore, new_row
from audio.worker.app import Engine, create_app
from tests.conftest import FakeG2P

PASSAGE = "The cat sat on the mat."


def canned_result() -> AudioResult:
    rows = [
        new_row(
            "substitution",
            passage_indices=[1],
            passage_words=["cat"],
            spoken=["cap"],
            start=0.5,
            end=0.9,
        )
    ]
    score = ReadingScore(
        counts={},  # type: ignore[typeddict-item]
        total_words=6,
        error_count=1,
        word_recognition_pct=83.3,
        wpm=72.0,
        reading_level="Frustration",
    )
    return AudioResult(version="6", score=score, words=rows, transcript=[])


class FakePipeline:
    def __init__(self, behaviour: Callable[[], AudioResult] = canned_result) -> None:
        self.behaviour = behaviour
        self.calls = 0

    def run(
        self,
        wav_path: str,
        passage_text: str,
        proper_nouns: Sequence[str] = (),
        deadline_ms: int | None = None,
    ) -> AudioResult:
        self.calls += 1
        return self.behaviour()


def write_wav(path: Path, *, rate: int = 16_000, channels: int = 1, frames: int = 1600) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * frames)
    return path


@pytest.fixture
def settings(tmp_path: Path) -> AudioSettings:
    return AudioSettings(
        WORKER_ALLOWED_MEDIA_DIR=tmp_path,
        WORKER_DISCONNECT_POLL_S=0.05,
        PHONETIC_ENABLED=False,
    )


@pytest.fixture
def pipeline() -> FakePipeline:
    return FakePipeline()


def make_client(settings: AudioSettings, pipeline: FakePipeline) -> TestClient:
    app = create_app(settings, lambda _s: Engine(pipeline, FakeG2P()))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(settings: AudioSettings, pipeline: FakePipeline) -> Iterator[TestClient]:
    with make_client(settings, pipeline) as c:
        wait_ready(c)
        yield c


def wait_ready(c: TestClient) -> None:
    for _ in range(100):
        if c.get("/health/ready").status_code == 200:
            return
        time.sleep(0.02)
    raise AssertionError("worker never became ready")


def job(path: Path, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "job_id": str(uuid4()),
        "deadline": int(time.time() * 1000) + 60_000,
        "wav_path": str(path),
        "passage_title": "Cat",
        "passage_content": PASSAGE,
    }
    body.update(over)
    return body


def assert_error(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


def test_ok_echoes_job_id(client: TestClient, tmp_path: Path) -> None:
    body = job(write_wav(tmp_path / "a.wav"))
    resp = client.post("/v1/jobs", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["job_id"] == body["job_id"]
    assert out["reading_score"] == 83.3
    assert out["miscues"][0]["miscue_type"] == "substitution"
    assert (out["miscues"][0]["start_ms"], out["miscues"][0]["end_ms"]) == (500, 900)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(job_id="not-a-uuid"),
        lambda b: b.pop("passage_content"),
        lambda b: b.update(deadline="soon"),
        lambda b: b.update(passage_content="   ...  "),
    ],
)
def test_invalid_job_body(
    client: TestClient, tmp_path: Path, mutate: Callable[[dict], Any]
) -> None:
    body = job(write_wav(tmp_path / "a.wav"))
    mutate(body)
    assert_error(client.post("/v1/jobs", json=body), 400, "INVALID_JOB")


def test_path_outside_allowed_dir(
    client: TestClient, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = write_wav(tmp_path_factory.mktemp("elsewhere") / "a.wav")
    assert_error(client.post("/v1/jobs", json=job(other)), 400, "INVALID_JOB")


def test_relative_path(client: TestClient) -> None:
    assert_error(client.post("/v1/jobs", json=job(Path("a.wav"))), 400, "INVALID_JOB")


def test_missing_file(client: TestClient, tmp_path: Path) -> None:
    assert_error(client.post("/v1/jobs", json=job(tmp_path / "gone.wav")), 422, "MEDIA_UNREADABLE")


def test_wrong_format_wav(client: TestClient, tmp_path: Path) -> None:
    path = write_wav(tmp_path / "hi.wav", rate=44_100, channels=2)
    assert_error(client.post("/v1/jobs", json=job(path)), 422, "MEDIA_UNREADABLE")


def test_zero_frame_wav(client: TestClient, tmp_path: Path) -> None:
    path = write_wav(tmp_path / "empty.wav", frames=0)
    assert_error(client.post("/v1/jobs", json=job(path)), 422, "MEDIA_UNREADABLE")


def test_bad_file_never_reaches_the_pipeline(
    client: TestClient, tmp_path: Path, pipeline: FakePipeline
) -> None:
    client.post("/v1/jobs", json=job(tmp_path / "gone.wav"))
    assert pipeline.calls == 0


def test_no_speech(settings: AudioSettings, tmp_path: Path) -> None:
    def raise_no_speech() -> AudioResult:
        raise NoSpeech()

    with make_client(settings, FakePipeline(raise_no_speech)) as c:
        wait_ready(c)
        resp = c.post("/v1/jobs", json=job(write_wav(tmp_path / "a.wav")))
    assert_error(resp, 422, "NO_SPEECH")


def test_deadline_already_passed(
    client: TestClient, tmp_path: Path, pipeline: FakePipeline
) -> None:
    body = job(write_wav(tmp_path / "a.wav"), deadline=int(time.time() * 1000) - 1)
    assert_error(client.post("/v1/jobs", json=body), 504, "DEADLINE_EXCEEDED")
    assert pipeline.calls == 0


def test_deadline_passes_while_queued(settings: AudioSettings, tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()

    def slow() -> AudioResult:
        started.set()
        release.wait(5)
        return canned_result()

    pipeline = FakePipeline(slow)
    wav = write_wav(tmp_path / "a.wav")
    results: dict[str, Any] = {}
    with make_client(settings, pipeline) as c:
        wait_ready(c)

        def first() -> None:
            results["first"] = c.post("/v1/jobs", json=job(wav))

        t = threading.Thread(target=first)
        t.start()
        assert started.wait(5)
        short = job(wav, deadline=int(time.time() * 1000) + 300)
        resp = c.post("/v1/jobs", json=short)
        release.set()
        t.join(10)
    assert_error(resp, 504, "DEADLINE_EXCEEDED")
    assert results["first"].status_code == 200
    assert pipeline.calls == 1  # the queued job never ran


def test_deadline_passes_during_the_run(settings: AudioSettings, tmp_path: Path) -> None:
    def slow() -> AudioResult:
        time.sleep(0.4)
        return canned_result()

    with make_client(settings, FakePipeline(slow)) as c:
        wait_ready(c)
        body = job(write_wav(tmp_path / "a.wav"), deadline=int(time.time() * 1000) + 200)
        resp = c.post("/v1/jobs", json=body)
    assert_error(resp, 504, "DEADLINE_EXCEEDED")


def test_pipeline_error_is_internal(settings: AudioSettings, tmp_path: Path) -> None:
    def boom() -> AudioResult:
        raise RuntimeError("secret passage text must not leak")

    with make_client(settings, FakePipeline(boom)) as c:
        wait_ready(c)
        resp = c.post("/v1/jobs", json=job(write_wav(tmp_path / "a.wav")))
    assert_error(resp, 500, "INTERNAL")
    assert "secret" not in resp.text


def test_ready_before_and_after_load(settings: AudioSettings) -> None:
    gate = threading.Event()

    def slow_factory(_s: AudioSettings) -> Engine:
        gate.wait(5)
        return Engine(FakePipeline(), FakeG2P())

    with TestClient(create_app(settings, slow_factory)) as c:
        resp = c.get("/health/ready")
        assert_error(resp, 503, "INTERNAL")
        gate.set()
        wait_ready(c)
        assert c.get("/health/ready").json() == {"status": "ready"}


def test_failed_load_stays_not_ready(settings: AudioSettings) -> None:
    def broken(_s: AudioSettings) -> Engine:
        raise RuntimeError("no GPU")

    with TestClient(create_app(settings, broken)) as c:
        time.sleep(0.2)
        assert_error(c.get("/health/ready"), 503, "INTERNAL")
        assert c.get("/stats").status_code == 200  # the process is alive


def test_stats(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/stats").json() == {"queue_length": 0, "avg_processing_seconds": None}
    client.post("/v1/jobs", json=job(write_wav(tmp_path / "a.wav")))
    stats = client.get("/stats").json()
    assert stats["queue_length"] == 0
    assert isinstance(stats["avg_processing_seconds"], float)
