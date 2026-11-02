"""Opt-in end-to-end check against a running stack (criterion 18).

    docker compose up -d --build
    READRIGHT_SMOKE_URL=https://localhost READRIGHT_SMOKE_API_KEY=... \
        uv run pytest tests/test_compose_smoke.py -q

Only READRIGHT_SMOKE_* variables are used: the conftest strips MAI_* ones.
"""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import boto3
import httpx
import pytest
from botocore.config import Config

BASE_URL = os.environ.get("READRIGHT_SMOKE_URL", "").rstrip("/")
API_KEY = os.environ.get("READRIGHT_SMOKE_API_KEY", "")
S3_URL = os.environ.get("READRIGHT_SMOKE_S3_URL", "http://localhost:5000")
REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
BUCKET = "readright-local"
PASSAGE = "Pam has a cat. It is on the bed. It can nap."

pytestmark = [
    pytest.mark.skipif(not BASE_URL, reason="READRIGHT_SMOKE_URL not set"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]


def s3_client(endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=REGION,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=Config(s3={"addressing_style": "path"}),
    )


def make_webm(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libvpx", "-c:a", "libopus", "-shortest", str(path),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip


def wait_until_ready(http: httpx.Client) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if http.get(f"{BASE_URL}/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    pytest.fail("stack did not become ready within 90 seconds")


def test_stack_analyzes_an_uploaded_recording_and_replays_a_repeat(tmp_path):
    """Criterion 18."""
    assert API_KEY, "set READRIGHT_SMOKE_API_KEY"
    request_id = str(uuid.uuid4())
    key = f"smoke/{request_id}.webm"
    webm = tmp_path / "reading.webm"
    make_webm(webm)

    uploader = s3_client(S3_URL)
    uploader.upload_file(str(webm), BUCKET, key)
    # The api container reaches moto as http://s3:5000, so presign against that host.
    video_url = s3_client("http://s3:5000").generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=900
    )
    body = {
        "request_id": request_id,
        "video_url": video_url,
        "passage_title": "Pam's Cat",
        "passage_content": PASSAGE,
    }
    headers = {"X-API-Key": API_KEY}

    with httpx.Client(verify=False, timeout=150) as http:
        wait_until_ready(http)
        first = http.post(f"{BASE_URL}/v1/analyses", json=body, headers=headers)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["request_id"] == request_id
        assert [m["miscue_type"] for m in result["miscues"]] == ["substitution", "omission"]
        for miscue in result["miscues"]:
            uploader.head_object(Bucket=BUCKET, Key=miscue["audio_object_key"])

        repeat = http.post(f"{BASE_URL}/v1/analyses", json=body, headers=headers)
        assert repeat.status_code == 200, repeat.text
        assert repeat.headers.get("X-MAI-Replayed") == "true"
        assert repeat.json() == result
