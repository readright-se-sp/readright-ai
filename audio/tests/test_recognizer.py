import math
import shutil
import subprocess
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from audio.errors import DeadlineExceeded, MediaUnreadable, RecognizerError
from audio.recognition.phoneme_recognizer import LoadedModel, Wav2Vec2PhonemeRecognizer

torch = pytest.importorskip("torch")

VOCAB = {"<pad>": 0, "|": 1, "a": 2, "b": 3}


class FakeModel:
    """Emits ids per frame from a fixed repeating pattern: blank, a, a, blank, b, blank."""

    pattern = [0, 2, 2, 0, 3, 0]

    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, feats):
        self.calls += 1
        if self.fail:
            raise RuntimeError("CUDA error: boom")
        frames = max(0, (feats.shape[1] - 400) // 320 + 1)
        logits = torch.full((1, frames, len(VOCAB)), -10.0)
        for t in range(frames):
            logits[0, t, self.pattern[t % len(self.pattern)]] = 10.0
        return SimpleNamespace(logits=logits)


def fake_extractor(samples, sampling_rate, return_tensors):
    return SimpleNamespace(input_values=torch.tensor(samples)[None, :])


def make(model=None, device="cpu", **kw):
    comps = LoadedModel(model or FakeModel(), fake_extractor, VOCAB, ["<pad>"], 0)
    return Wav2Vec2PhonemeRecognizer("fake", device=device, components=comps, **kw)


def write(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(int(seconds * 16000), dtype="<i2").tobytes())
    return path


def test_ready_after_construction():
    assert make().ready


def test_recognise_with_fake_model(tmp_path):
    toks = make().recognise(write(tmp_path / "a.wav", 0.6), None)
    assert [(t.symbol, t.start_ms, t.end_ms) for t in toks[:3]] == [
        ("a", 20, 60),
        ("b", 80, 100),
        ("a", 140, 180),
    ]
    assert all(0 <= t.start_ms <= t.end_ms <= 600 for t in toks)


def test_multi_chunk_matches_single_chunk(tmp_path):
    path = write(tmp_path / "a.wav", 5)
    one = make(chunk_s=20, context_s=2).recognise(path, None)
    # the pattern repeats every 6 frames; the 96-frame core and 48-frame context are multiples
    many = make(chunk_s=1.92, context_s=0.96).recognise(path, None)
    assert [(t.symbol, t.start_ms) for t in many] == [(t.symbol, t.start_ms) for t in one]


def test_past_deadline_raises_before_inference(tmp_path):
    model = FakeModel()
    rec = make(model)
    calls_after_warmup = model.calls
    with pytest.raises(DeadlineExceeded):
        rec.recognise(write(tmp_path / "a.wav", 1), int(time.time() * 1000) - 1)
    assert model.calls == calls_after_warmup


def test_future_deadline_is_fine(tmp_path):
    assert make().recognise(write(tmp_path / "a.wav", 1), int(time.time() * 1000) + 60_000)


def test_model_exception_is_wrapped(tmp_path):
    rec = make()
    rec._loaded.model.fail = True
    with pytest.raises(RecognizerError) as info:
        rec.recognise(write(tmp_path / "a.wav", 1), None)
    assert isinstance(info.value.__cause__, RuntimeError)


def test_bad_wav_raises_media_unreadable(tmp_path):
    with pytest.raises(MediaUnreadable):
        make().recognise(tmp_path / "missing.wav", None)


@pytest.mark.gpu
def test_recogniser_runs_on_cuda(tmp_path):
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    assert make(device="cuda").recognise(write(tmp_path / "a.wav", 1), None)


# --- integration: the real model and the espeak-ng CLI ---


def _espeak_cli():
    found = shutil.which("espeak-ng")
    if found:
        return found
    default = Path(r"C:\Program Files\eSpeak NG\espeak-ng.exe")
    return str(default) if default.exists() else None


def levenshtein(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


@pytest.mark.model
@pytest.mark.espeak
def test_real_model_on_synthesised_speech(tmp_path, espeak_g2p):
    cli = _espeak_cli()
    if cli is None:
        pytest.skip("the espeak-ng CLI is not available")
    from audio.config import AudioSettings

    settings = AudioSettings(_env_file=None)
    sentence = "the cat sat on the mat"
    raw = tmp_path / "raw.wav"
    subprocess.run([cli, "-v", "en-us", "-w", str(raw), sentence], check=True)
    with wave.open(str(raw), "rb") as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    n_out = int(len(data) * 16000 / rate)  # test-only resample
    x = np.interp(np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data.astype(float))
    wav = tmp_path / "speech.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(x.astype("<i2").tobytes())

    try:
        rec = Wav2Vec2PhonemeRecognizer(
            settings.PHONEME_MODEL,
            settings.PHONEME_DEVICE,
            settings.PHONEME_FP16,
            settings.PHONEME_CHUNK_S,
            settings.PHONEME_CONTEXT_S,
        )
    except OSError as exc:
        pytest.skip(f"model weights are not available: {exc}")
    toks = rec.recognise(wav, None)
    ref = [s for seq in espeak_g2p.phonemize(sentence.split()) for s in seq]
    per = levenshtein([t.symbol for t in toks], ref) / len(ref)
    assert per <= 0.35, per
    starts = [t.start_ms for t in toks]
    assert starts == sorted(starts)
    assert all(t.end_ms <= math.ceil(n_out / 16) for t in toks)
