import io
import wave
from pathlib import Path

from .errors import MAIError
from .models import ErrorCode


def wav_duration_ms(wav: Path) -> int:
    with wave.open(str(wav), "rb") as src:
        return src.getnframes() * 1000 // src.getframerate()


def cut_clip(wav: Path, start_ms: int, end_ms: int, padding_ms: int) -> bytes:
    """Return a complete WAV holding [start - padding, end + padding], clamped to the file."""
    duration_ms = wav_duration_ms(wav)
    if start_ms >= duration_ms:
        raise MAIError(
            ErrorCode.PROCESSING_FAILED,
            "The analysis could not be completed.",
            {"lane": "audio"},
        )
    start = max(0, start_ms - padding_ms)
    end = min(duration_ms, end_ms + padding_ms)
    with wave.open(str(wav), "rb") as src:
        rate = src.getframerate()
        first = start * rate // 1000
        last = max(first, end * rate // 1000)
        src.setpos(first)
        frames = src.readframes(last - first)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as dst:
            dst.setnchannels(src.getnchannels())
            dst.setsampwidth(src.getsampwidth())
            dst.setframerate(rate)
            dst.writeframes(frames)
    return buffer.getvalue()
