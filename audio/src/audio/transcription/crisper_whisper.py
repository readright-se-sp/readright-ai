import re
from typing import Any, Protocol

import whisperx  # type: ignore[import-untyped]
from whisperx.asr import WhisperModel  # type: ignore[import-untyped]

from audio.config import AudioSettings
from audio.models import SpokenWord

_EVENT_TOKEN = re.compile(r"\[[A-Za-z]+\]")  # [UM] [UH] [laughter] ... (not [verbatim_1])


class TranscriberProtocol(Protocol):
    """Interface for ASR — pipeline depends on this, tests pass a fake."""

    def transcribe(self, wav_path: str) -> list[SpokenWord]: ...


class _VerbatimWhisperModel(WhisperModel):  # type: ignore[misc]
    """WhisperX's batched model, decoding CrisperWhisper 2.0 in verbatim mode.

    Out of the box WhisperX knows neither of the model's extras, so it (1) sends no mode tags,
    which leaves the model unconditioned and drops cut-offs and repeats ('outw- out', 'said and
    said'), and (2) keeps only tokens below <|endoftext|>, deleting the fillers and vocal sounds
    ([UM], [UH], [laughter]: ids above it). Prompt layout as in the official package
    (crisperwhisper/prompt.py): [verbatim_1]..[verbatim_5] <|startoftranscript|><|en|>...
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        vocab: dict[str, int] = self.hf_tokenizer.get_vocab(with_added_tokens=True)
        self._mode_tags = [vocab.get(f"[verbatim_{i}]") for i in range(1, 6)]
        if None in self._mode_tags:
            raise ValueError("not a CrisperWhisper 2.0 model: no [verbatim_N] tokens")
        self._events = {i: t for t, i in vocab.items() if _EVENT_TOKEN.fullmatch(t)}

    def get_prompt(self, tokenizer: Any, previous_tokens: Any, *args: Any, **kwargs: Any) -> list[int]:
        return self._mode_tags + super().get_prompt(tokenizer, previous_tokens, *args, **kwargs)

    def generate_segment_batched(
        self, features: Any, tokenizer: Any, options: Any, encoder_output: Any = None
    ) -> dict[str, list[Any]]:
        """WhisperX's version (minus initial_prompt, which we never use) with event tokens kept."""
        prompt = self.get_prompt(
            tokenizer, [], without_timestamps=options.without_timestamps, prefix=options.prefix,
            hotwords=options.hotwords,
        )
        results: Any = self.model.generate(
            self.encode(features),
            [prompt] * features.shape[0],
            beam_size=options.beam_size,
            patience=options.patience,
            length_penalty=options.length_penalty,
            max_length=self.max_length,
            suppress_blank=options.suppress_blank,
            suppress_tokens=options.suppress_tokens,
            no_repeat_ngram_size=options.no_repeat_ngram_size,
            repetition_penalty=options.repetition_penalty,
            return_scores=True,
        )
        texts: list[str] = []
        avg_logprobs: list[float] = []
        for res in results:
            ids: list[int] = res.sequences_ids[0]
            texts.append(self._decode(ids, tokenizer.eot))
            avg_logprobs.append(res.scores[0] * (len(ids) ** options.length_penalty) / (len(ids) + 1))
        return {"text": texts, "avg_logprob": avg_logprobs}

    def _decode(self, ids: list[int], eot: int) -> str:
        """Text tokens decoded as usual; each event token becomes its own word ('the [UM] dog')."""
        parts: list[str] = []
        run: list[int] = []
        for t in ids:
            if t < eot:
                run.append(t)
            elif t in self._events:
                parts += [self.hf_tokenizer.decode(run), self._events[t]]
                run = []
        parts.append(self.hf_tokenizer.decode(run))
        return " ".join(p.strip() for p in parts if p.strip())


class CrisperWhisperTranscriber:
    """CrisperWhisper 2.0 (CTranslate2, verbatim mode) through WhisperX + wav2vec2 word alignment. Load once."""

    def __init__(self, settings: AudioSettings) -> None:
        """Stores config only — call load() at service startup."""
        self._settings = settings
        self._model: Any = None
        self._align_model: Any = None
        self._metadata: Any = None

    def load(self) -> None:
        """Loads ASR + alignment models into memory."""
        s = self._settings
        model = _VerbatimWhisperModel(s.ASR_MODEL, device=s.ASR_DEVICE, compute_type=s.ASR_COMPUTE_TYPE)
        self._model = whisperx.load_model(  # type: ignore[no-untyped-call]
            s.ASR_MODEL,
            device=s.ASR_DEVICE,
            compute_type=s.ASR_COMPUTE_TYPE,
            language="en",
            model=model,
            asr_options={
                # >1 / >0 would delete the repeats a verbatim model keeps.
                "repetition_penalty": 1.0,
                "no_repeat_ngram_size": 0,
                # Never prompt with the passage — the model would "hear" it.
                "initial_prompt": None,
                # As the official package decodes: [-1] = the model's own suppress list, no blank suppression.
                "suppress_tokens": [-1],
                "suppress_blank": False,
            },
        )
        self._align_model, self._metadata = whisperx.load_align_model(  # type: ignore[no-untyped-call]
            language_code="en",
            device=s.ASR_DEVICE,
        )

    def transcribe(self, wav_path: str) -> list[SpokenWord]:
        """Verbatim words with timing. Words the aligner can't place keep None timing."""
        if self._model is None:
            raise RuntimeError("CrisperWhisperTranscriber.load() was not called")
        audio: Any = whisperx.load_audio(wav_path)  # type: ignore[no-untyped-call]
        result: Any = self._model.transcribe(audio, batch_size=self._settings.ASR_BATCH_SIZE, language="en")
        aligned: Any = whisperx.align(  # type: ignore[no-untyped-call]
            result["segments"],
            self._align_model,
            self._metadata,
            audio,
            self._settings.ASR_DEVICE,
            return_char_alignments=False,
        )
        return [self._to_word(seg) for seg in aligned.get("word_segments", [])]

    @staticmethod
    def _to_word(seg: Any) -> SpokenWord:
        return SpokenWord(
            text=str(seg.get("word", "")).strip(),
            start=_opt_float(seg.get("start")),
            end=_opt_float(seg.get("end")),
            score=_opt_float(seg.get("score")),
        )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)
