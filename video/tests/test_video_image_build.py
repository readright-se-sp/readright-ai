import subprocess
import time
import uuid

import pytest
from packaging_helpers import VIDEO_DIR, docker_ready

pytestmark = pytest.mark.skipif(not docker_ready(), reason="docker daemon not available")

IMAGE = "readright-mai-video:test"
BUILD_TIMEOUT = 1800
READY_PROBE = (
    "import urllib.request,sys;"
    "sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/health/ready',timeout=4)"
    ".status==200 else 1)"
)

ANALYZE_SCRIPT = """
import mediapipe
import logging
import time
from pathlib import Path
from app.analyzer import JobContext, MediaPipeAnalyzer
from app.config import Settings

records = []


class Capture(logging.Handler):
    def emit(self, record):
        records.append(record)


logging.getLogger("app.analyzer").addHandler(Capture())
logging.getLogger("app.analyzer").setLevel(logging.INFO)

analyzer = MediaPipeAnalyzer(Settings(_env_file=None))
analyzer.load()
ctx = JobContext(job_id='image-check', deadline_ms=int(time.time() * 1000) + 120000)
flags = analyzer.analyze(Path('/scratch/testsrc.webm'), ctx)
done = [r for r in records if r.getMessage() == 'analysis finished']
print('FLAGS', flags.is_finger_pointing, flags.is_lack_expression)
print('FRAMES', done[-1].frames if done else -1)
"""


@pytest.fixture(scope="module")
def image():
    done = subprocess.run(
        ["docker", "build", "-t", IMAGE, str(VIDEO_DIR)],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    return IMAGE


def docker(*args: str, timeout: int = 120):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def test_image_builds_and_serves_ready_within_the_start_period(image):
    """Criterion 6."""
    name = f"readright-video-test-{uuid.uuid4().hex[:8]}"
    try:
        started = docker("run", "-d", "--name", name, image)
        assert started.returncode == 0, started.stderr
        deadline = time.monotonic() + 150
        ready = False
        status = ""
        while time.monotonic() < deadline:
            if not ready:
                ready = docker("exec", name, "python", "-c", READY_PROBE).returncode == 0
            inspected = docker("inspect", "--format", "{{.State.Health.Status}}", name)
            status = inspected.stdout.strip()
            if ready and status == "healthy":
                break
            time.sleep(2)
        if not (ready and status == "healthy"):
            logs = docker("logs", name)
            pytest.fail(
                f"ready={ready} health={status!r} after 150 s"
                "\n" + logs.stdout[-2000:] + logs.stderr[-2000:]
            )
    finally:
        docker("rm", "-f", name)


def test_mediapipe_analyzes_a_clip_from_a_read_only_scratch_volume_as_the_default_user(image):
    """Criterion 7."""
    volume = f"readright-video-scratch-{uuid.uuid4().hex[:8]}"
    try:
        created = docker("volume", "create", volume)
        assert created.returncode == 0, created.stderr
        populate = (
            "set -e; ffmpeg -loglevel error -y -f lavfi "
            "-i testsrc=duration=3:size=320x240:rate=15 -c:v libvpx /scratch/testsrc.webm; "
            "chown 1000:1000 /scratch/testsrc.webm"
        )
        filled = docker(
            "run", "--rm", "--user", "0", "-v", f"{volume}:/scratch", image,
            "sh", "-c", populate,
            timeout=300,
        )  # fmt: skip
        assert filled.returncode == 0, filled.stderr[-2000:]
        done = docker(
            "run", "--rm", "-v", f"{volume}:/scratch:ro", image,
            "python", "-c", ANALYZE_SCRIPT,
            timeout=600,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-3000:]
        assert "FLAGS False False" in done.stdout
        frames = int(done.stdout.split("FRAMES", 1)[1].split()[0])
        assert frames > 0
    finally:
        docker("volume", "rm", "-f", volume)
