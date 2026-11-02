"""Whole-recording phoneme recogniser: wav2vec2 CTC, greedy decoding, 20 ms frame times."""

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from audio.errors import DeadlineExceeded, RecognizerError
from audio.recognition import ctc
from audio.recognition.audio_io import SAMPLE_RATE, load_wav
from audio.recognition.vocab import build_token_map, spans_to_tokens
from audio.types import PhonemeToken

logger = logging.getLogger(__name__)

_MIN_SAMPLES = 1600  # shorter chunk inputs are zero-padded so the conv stack has frames to emit


class PhonemeRecognizer(Protocol):
    def recognise(self, wav_path: Path, deadline_ms: int | None) -> tuple[PhonemeToken, ...]:
        """``deadline_ms`` is an absolute UTC epoch time in milliseconds (worker contract)."""
        ...

    @property
    def ready(self) -> bool: ...


@dataclass
class LoadedModel:
    """What the recogniser needs from Hugging Face; tests inject a fake."""

    model: Any  # callable(input_values) -> object with .logits [1, T, V]
    feature_extractor: Callable[..., Any]
    vocab: dict[str, int]
    special_tokens: list[str]
    blank_id: int


def _load_hf(model_id: str, device: str, fp16: bool) -> LoadedModel:
    from transformers import AutoFeatureExtractor, AutoModelForCTC, Wav2Vec2CTCTokenizer

    # Load the feature extractor and vocab through the base CTC tokenizer rather
    # than AutoProcessor / Wav2Vec2PhonemeCTCTokenizer, which instantiates an espeak
    # backend on import — unnecessary because we never call its phonemize path.
    feature_extractor = AutoFeatureExtractor.from_pretrained(model_id)
    tok = Wav2Vec2CTCTokenizer.from_pretrained(model_id)
    model = AutoModelForCTC.from_pretrained(model_id).eval().to(device)
    if fp16:
        model = model.half()
    return LoadedModel(
        model=model,
        feature_extractor=feature_extractor,
        vocab=tok.get_vocab(),
        special_tokens=list(tok.all_special_tokens),
        blank_id=int(tok.pad_token_id),
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


class Wav2Vec2PhonemeRecognizer:
    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        fp16: bool = False,
        chunk_s: float = 20,
        context_s: float = 2,
        *,
        components: LoadedModel | None = None,
    ) -> None:
        import torch

        self._torch = torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.fp16 = bool(fp16 and device.startswith("cuda"))
        self.chunk_s, self.context_s = chunk_s, context_s
        self._lock = threading.Lock()
        self._ready = False

        self._loaded = components or _load_hf(model_id, device, self.fp16)
        self.token_map = build_token_map(self._loaded.vocab, self._loaded.special_tokens)
        self._blank_id = self._loaded.blank_id
        self._logprobs_chunk(np.zeros(SAMPLE_RATE, dtype=np.float32))  # warm-up
        self._ready = True
        logger.info("phoneme recogniser ready: %s on %s (fp16=%s)", model_id, device, self.fp16)

    @property
    def ready(self) -> bool:
        return self._ready

    def _logprobs_chunk(self, samples: np.ndarray) -> np.ndarray:
        torch = self._torch
        if len(samples) < _MIN_SAMPLES:
            samples = np.pad(samples, (0, _MIN_SAMPLES - len(samples)))
        try:
            with self._lock, torch.inference_mode():
                feats = self._loaded.feature_extractor(
                    samples, sampling_rate=SAMPLE_RATE, return_tensors="pt"
                ).input_values.to(self.device)
                if self.fp16:
                    feats = feats.half()
                logits = self._loaded.model(feats).logits
                return logits.float().log_softmax(dim=-1)[0].cpu().numpy()
        except Exception as exc:
            raise RecognizerError(f"inference failed: {exc}") from exc

    @staticmethod
    def _check_deadline(deadline_ms: int | None) -> None:
        if deadline_ms is not None and _now_ms() >= deadline_ms:
            raise DeadlineExceeded("the job deadline has passed")

    def logprobs(self, samples: np.ndarray, deadline_ms: int | None = None) -> np.ndarray:
        """Stitched per-frame log-probabilities ``[T, V]`` (fp32) for the whole recording."""
        chunks = ctc.plan_chunks(len(samples), SAMPLE_RATE, self.chunk_s, self.context_s)
        outputs = []
        for chunk in chunks:
            self._check_deadline(deadline_ms)
            outputs.append(self._logprobs_chunk(samples[chunk.in_start : chunk.in_end]))
        return ctc.stitch(outputs, chunks)

    def recognise(self, wav_path: Path, deadline_ms: int | None) -> tuple[PhonemeToken, ...]:
        samples = load_wav(wav_path)
        self._check_deadline(deadline_ms)
        logprobs = self.logprobs(samples, deadline_ms)
        audio_ms = math.ceil(len(samples) * 1000 / SAMPLE_RATE)
        spans = ctc.collapse(logprobs, self._blank_id)
        return spans_to_tokens(spans, self.token_map, audio_ms)
