"""Slow end-to-end check: ASR + Lane A + Lane B on one real recording (needs GPU and data)."""

from pathlib import Path

import pytest

from audio.config import AudioSettings
from audio.wiring import (
    build_audio_pipeline,
    build_phonetic_reviewer,
    load_phoneme_recognizer,
    load_transcriber,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.gpu
@pytest.mark.model
@pytest.mark.espeak
def test_full_pipeline_on_one_recording(espeak_g2p):
    import torch

    from devtools.recordings import passage_for

    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    wavs = sorted((ROOT / "data").glob("ring*.wav"))
    if not wavs:
        pytest.skip("no recording in audio/data/ (student audio is not in git)")
    settings = AudioSettings(_env_file=None)
    if not (ROOT / settings.ASR_MODEL).exists():
        pytest.skip(f"ASR model not found at {settings.ASR_MODEL}")

    recogniser = load_phoneme_recognizer(settings)
    assert recogniser is not None
    reviewer = build_phonetic_reviewer(recogniser, settings)
    pipeline = build_audio_pipeline(load_transcriber(settings), reviewer)
    passage = passage_for(wavs[0])

    result = pipeline.run(str(wavs[0]), passage)

    assert result["version"] == "5"
    judged = [w for w in result["words"] if "phonetic" in w]
    assert judged, "Lane B judged no rows"
    assert any(w["phonetic"]["check"] == "done" for w in judged)
    covered = sorted(i for w in result["words"] for i in w["passage_indices"])
    assert covered == list(range(len(covered)))  # every passage word in exactly one row
    assert reviewer.last_timing_ms["recognise_ms"] > 0
