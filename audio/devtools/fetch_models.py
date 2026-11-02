"""Download and prepare every model the audio worker needs, at image build time.

Reads models.lock (pinned Hugging Face revisions) and writes under --model-dir:

    crisper2_large_ct2/   CrisperWhisper 2.0 large, converted to CTranslate2 float16
    phoneme/              facebook/wav2vec2-xlsr-53-espeak-cv-ft (Lane B)
    torch/hub/checkpoints the whisperx English alignment model (torch hub cache)

The build fails if the converted ASR model has lost its [verbatim_N] tokens. Run from audio/:

    uv run python -m devtools.fetch_models --model-dir D:/dev-cache/audio-models
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

LOCK = Path(__file__).resolve().parent.parent / "models.lock"
VERBATIM_TOKENS = tuple(f"[verbatim_{i}]" for i in range(1, 6))
ASR_DIR = "crisper2_large_ct2"
PHONEME_DIR = "phoneme"
# Files the CTranslate2 converter does not write but the tokenizer and feature extractor need.
COPY_FILES = ["tokenizer.json", "preprocessor_config.json"]


class LockEntry(NamedTuple):
    name: str
    kind: str
    source: str
    revision: str


def read_lock(path: Path = LOCK) -> dict[str, LockEntry]:
    entries: dict[str, LockEntry] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"bad models.lock line: {line!r}")
        entry = LockEntry(*parts)
        if entry.kind == "hf" and len(entry.revision) != 40:
            raise ValueError(f"{entry.name}: revision must be a 40-character commit hash")
        entries[entry.name] = entry
    return entries


def check_verbatim_tokens(model_dir: Path) -> None:
    """Raise if the converted model lacks the CrisperWhisper 2.0 verbatim tokens."""
    vocab = json.loads((model_dir / "vocabulary.json").read_text(encoding="utf-8"))
    tokenizer = (model_dir / "tokenizer.json").read_text(encoding="utf-8")
    missing = [t for t in VERBATIM_TOKENS if t not in vocab or t not in tokenizer]
    if missing:
        raise RuntimeError(f"the converted ASR model has lost its verbatim tokens: {missing}")


def fetch_asr(entry: LockEntry, model_dir: Path) -> Path:
    from ctranslate2.converters import TransformersConverter
    from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

    out = model_dir / ASR_DIR
    if out.is_dir():
        check_verbatim_tokens(out)
        return out
    with tempfile.TemporaryDirectory(dir=model_dir) as tmp:
        src = snapshot_download(
            entry.source,
            revision=entry.revision,
            local_dir=Path(tmp) / "src",
            allow_patterns=["*.json", "*.txt", "model.safetensors"],
        )
        converter = TransformersConverter(src, copy_files=COPY_FILES)
        converter.convert(str(out), quantization="float16")
    check_verbatim_tokens(out)  # a failed check leaves nothing behind
    return out


def fetch_phoneme(entry: LockEntry, model_dir: Path) -> Path:
    from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

    out = model_dir / PHONEME_DIR
    snapshot_download(entry.source, revision=entry.revision, local_dir=out)
    return out


def fetch_align(entry: LockEntry, model_dir: Path) -> None:
    """whisperx downloads the torchaudio bundle into TORCH_HOME/hub/checkpoints."""
    os.environ["TORCH_HOME"] = str(model_dir / "torch")
    import whisperx

    whisperx.load_align_model(language_code="en", device="cpu")  # type: ignore[no-untyped-call]
    checkpoints = model_dir / "torch" / "hub" / "checkpoints"
    if not any(checkpoints.glob("*")):
        raise RuntimeError("the alignment model was not downloaded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and convert the audio worker models.")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=LOCK)
    args = parser.parse_args(argv)
    lock = read_lock(args.lock)
    args.model_dir.mkdir(parents=True, exist_ok=True)
    try:
        fetch_asr(lock["crisperwhisper"], args.model_dir)
        fetch_phoneme(lock["phoneme"], args.model_dir)
        fetch_align(lock["align"], args.model_dir)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
