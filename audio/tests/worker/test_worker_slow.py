"""Slow check: the real worker app (ASR + Lane A, Lane B off) on one real recording."""

import shutil
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from audio.config import AudioSettings
from audio.worker.app import create_app
from audio.worker.schemas import AudioWorkerResult

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.gpu
@pytest.mark.model
@pytest.mark.espeak
def test_real_worker_on_one_recording(tmp_path: Path, espeak_g2p: object) -> None:
    import torch

    from devtools.recordings import passage_for

    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    wavs = sorted((ROOT / "data").glob("ring*.wav"))
    if not wavs:
        pytest.skip("no recording in audio/data/ (student audio is not in git)")
    settings = AudioSettings(_env_file=None, WORKER_ALLOWED_MEDIA_DIR=tmp_path)  # type: ignore[call-arg]
    if not (ROOT / settings.ASR_MODEL).exists():
        pytest.skip(f"ASR model not found at {settings.ASR_MODEL}")
    wav = tmp_path / "audio.wav"
    shutil.copy(wavs[0], wav)

    with TestClient(create_app(settings)) as client:
        for _ in range(600):  # model load
            if client.get("/health/ready").status_code == 200:
                break
            time.sleep(0.5)
        else:
            pytest.fail("the worker never became ready")
        body = {
            "job_id": str(uuid4()),
            "deadline": int(time.time() * 1000) + 120_000,
            "wav_path": str(wav),
            "passage_title": "slow test",
            "passage_content": passage_for(wavs[0]),
        }
        start = time.perf_counter()
        resp = client.post("/v1/jobs", json=body)
        wall = time.perf_counter() - start

    assert resp.status_code == 200, resp.text
    result = AudioWorkerResult.model_validate(resp.json())
    assert str(result.job_id) == body["job_id"]
    assert all(m.start_ms <= m.end_ms for m in result.miscues)
    print(f"worker wall time: {wall:.1f} s, {len(result.miscues)} miscues")
