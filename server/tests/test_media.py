"""Phase 3: subprocess runner, ffprobe and ffmpeg wrappers (criteria 15-19)."""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest
from conftest import API_KEY

from app import media
from app.config import Settings
from app.errors import MAIError
from app.media import ProbeResult, convert_to_wav, probe, run_process
from app.models import ErrorCode

pytestmark = pytest.mark.anyio

SLEEPER = "import time,sys; time.sleep(1.5); open(sys.argv[1],'w').close()"


@pytest.fixture
def media_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        api_key=API_KEY,
        pipeline_mode="stub",
        ffmpeg_path="my-ffmpeg",
        ffprobe_path="my-ffprobe",
    )


# 15. run_process --------------------------------------------------------------------------------


async def test_c15_run_process_returns_code_stdout_stderr():
    code = "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)"
    returncode, out, err = await run_process([sys.executable, "-c", code])
    assert (returncode, out, err) == (3, b"out", b"err")


async def test_c15_stdin_is_devnull():
    code = "import sys; sys.stdout.write(repr(sys.stdin.read()))"
    returncode, out, _ = await asyncio.wait_for(run_process([sys.executable, "-c", code]), 10)
    assert returncode == 0
    assert out == b"''"


async def test_c15_missing_binary_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        await run_process(["definitely-not-a-real-binary-xyz"])


async def _raise_not_implemented(*args, **kwargs):
    raise NotImplementedError


@pytest.fixture(params=["subprocess_exec", "popen_fallback"])
def runner_mode(request, monkeypatch):
    if request.param == "popen_fallback":
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_not_implemented)
    return request.param


async def test_c15_run_process_works_in_both_modes(runner_mode):
    code = "import sys; sys.stdout.write('hi'); sys.stderr.write('e')"
    assert await run_process([sys.executable, "-c", code]) == (0, b"hi", b"e")


async def test_c15_missing_binary_in_both_modes(runner_mode):
    with pytest.raises(FileNotFoundError):
        await run_process(["definitely-not-a-real-binary-xyz"])


async def test_c15_cancellation_kills_child(runner_mode, tmp_path):
    marker = tmp_path / "marker"
    task = asyncio.create_task(run_process([sys.executable, "-c", SLEEPER, str(marker)]))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.5)  # long enough for a surviving child to create the marker
    assert not marker.exists()


async def test_c15_uncancelled_child_does_create_marker(tmp_path):
    """Control for the cancellation test: the child really writes the marker if left alone."""
    marker = tmp_path / "marker"
    returncode, _, _ = await run_process([sys.executable, "-c", SLEEPER, str(marker)])
    assert returncode == 0
    assert marker.exists()


# 16. probe command and parsing ------------------------------------------------------------------


def _probe_json(format_name="matroska,webm", streams=("video", "audio"), duration="1.5"):
    return json.dumps(
        {
            "format": {"format_name": format_name, "duration": duration},
            "streams": [{"codec_type": kind} for kind in streams],
        }
    ).encode()


def _fake_run(monkeypatch, result=(0, b"", b""), exc=None):
    calls: list[list[str]] = []

    async def fake(args):
        calls.append(list(args))
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(media, "run_process", fake)
    return calls


async def test_c16_probe_runs_ffprobe_with_exact_args(monkeypatch, media_settings, tmp_path):
    calls = _fake_run(monkeypatch, (0, _probe_json(), b""))
    path = tmp_path / "input.webm"
    result = await probe(path, media_settings)
    assert calls == [
        [
            "my-ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    ]
    assert result == ProbeResult("matroska,webm", True, True, 1.5)


async def test_c16_probe_result_is_frozen_dataclass():
    result = ProbeResult("webm", True, True, None)
    with pytest.raises(AttributeError):
        result.has_audio = False  # type: ignore[misc]
    assert result.duration_seconds is None


async def test_c16_probe_without_duration_gives_none(monkeypatch, media_settings, tmp_path):
    payload = json.dumps(
        {
            "format": {"format_name": "webm"},
            "streams": [{"codec_type": "audio"}, {"codec_type": "video"}],
        }
    ).encode()
    _fake_run(monkeypatch, (0, payload, b""))
    result = await probe(tmp_path / "x", media_settings)
    assert result.duration_seconds is None
    assert result.format_name == "webm"


@pytest.mark.parametrize("format_name", ["webm", "matroska,webm", "matroska"])
async def test_c16_accepted_formats(monkeypatch, media_settings, tmp_path, format_name):
    _fake_run(monkeypatch, (0, _probe_json(format_name=format_name), b""))
    result = await probe(tmp_path / "x", media_settings)
    assert result.has_audio and result.has_video


# 17. probe failures -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        (1, b"", b"Invalid data found"),
        (0, b"this is not json", b""),
        (0, _probe_json(format_name="mov,mp4,m4a,3gp,3g2,mj2"), b""),
        (0, _probe_json(format_name="wav"), b""),
        (0, _probe_json(streams=("video",)), b""),
        (0, _probe_json(streams=("audio",)), b""),
        (0, _probe_json(streams=()), b""),
    ],
    ids=[
        "nonzero_exit",
        "bad_json",
        "mp4",
        "wav",
        "video_only",
        "audio_only",
        "no_streams",
    ],
)
async def test_c17_probe_rejects_with_unsupported_media(
    monkeypatch, media_settings, tmp_path, result
):
    _fake_run(monkeypatch, result)
    with pytest.raises(MAIError) as exc:
        await probe(tmp_path / "x", media_settings)
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA
    assert exc.value.status == 415


async def test_c17_missing_ffprobe_is_processing_failed(monkeypatch, media_settings, tmp_path):
    _fake_run(monkeypatch, exc=FileNotFoundError("my-ffprobe"))
    with pytest.raises(MAIError) as exc:
        await probe(tmp_path / "x", media_settings)
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.status == 500


# 18. convert ------------------------------------------------------------------------------------


def _fake_convert(monkeypatch, *, returncode=0, stderr=b"", create=True, exc=None):
    calls: list[list[str]] = []

    async def fake(args):
        calls.append(list(args))
        if exc is not None:
            raise exc
        if create and returncode == 0:
            Path(args[-1]).write_bytes(b"RIFF")
        return returncode, b"", stderr

    monkeypatch.setattr(media, "run_process", fake)
    return calls


async def test_c18_convert_runs_ffmpeg_with_exact_args(monkeypatch, media_settings, tmp_path):
    calls = _fake_convert(monkeypatch)
    src, dest = tmp_path / "input.webm", tmp_path / "audio.wav"
    await convert_to_wav(src, dest, media_settings)
    assert calls == [
        [
            "my-ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    ]
    assert dest.exists()


@pytest.mark.parametrize("case", ["nonzero", "no_output", "missing_binary"])
async def test_c18_failures_are_processing_failed(monkeypatch, media_settings, tmp_path, case):
    if case == "nonzero":
        _fake_convert(monkeypatch, returncode=1, stderr=b"boom")
    elif case == "no_output":
        _fake_convert(monkeypatch, create=False)
    else:
        _fake_convert(monkeypatch, exc=FileNotFoundError("my-ffmpeg"))
    with pytest.raises(MAIError) as exc:
        await convert_to_wav(tmp_path / "in.webm", tmp_path / "out.wav", media_settings)
    assert exc.value.code == ErrorCode.PROCESSING_FAILED
    assert exc.value.status == 500


async def test_c18_stderr_tail_is_logged_but_never_returned(
    monkeypatch, media_settings, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    stderr = b"HEADMARK" + b"x" * 2000 + b"TAILMARK"
    _fake_convert(monkeypatch, returncode=1, stderr=stderr)
    with pytest.raises(MAIError) as exc:
        await convert_to_wav(tmp_path / "in.webm", tmp_path / "out.wav", media_settings)

    returned = f"{exc.value.message} {exc.value.details}"
    assert "TAILMARK" not in returned and "xxxx" not in returned

    logged = "\n".join(r.getMessage() + " " + str(r.__dict__) for r in caplog.records)
    assert "TAILMARK" in logged
    assert "HEADMARK" not in logged  # only the last 500 chars


# 19. real ffmpeg --------------------------------------------------------------------------------


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed; real-media tests need both on PATH")


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=60,
    )


@pytest.fixture(scope="session")
def media_files(tmp_path_factory) -> dict[str, Path]:
    """Tiny generated fixtures. Skips when ffmpeg or ffprobe is missing."""
    _need_ffmpeg()
    root = tmp_path_factory.mktemp("media")
    sine = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    color = ["-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10"]
    files = {
        "av_webm": root / "av.webm",
        "video_only_webm": root / "video_only.webm",
        "wav": root / "tone.wav",
        "mp4": root / "av.mp4",
        "garbage": root / "garbage.bin",
    }
    _ffmpeg(*sine, *color, "-c:v", "libvpx", "-c:a", "libopus", str(files["av_webm"]))
    _ffmpeg(*color, "-c:v", "libvpx", str(files["video_only_webm"]))
    _ffmpeg(*sine, str(files["wav"]))
    _ffmpeg(*sine, *color, "-c:v", "mpeg4", "-c:a", "aac", str(files["mp4"]))
    files["garbage"].write_bytes(bytes(range(256)) * 40)
    return files


@pytest.fixture
def real_settings() -> Settings:
    return Settings(_env_file=None, api_key=API_KEY, pipeline_mode="stub")  # type: ignore[call-arg]


async def test_c19_convert_real_webm_to_16k_mono_pcm(media_files, real_settings, tmp_path):
    dest = tmp_path / "audio.wav"
    await convert_to_wav(media_files["av_webm"], dest, real_settings)
    with wave.open(str(dest), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() > 0


async def test_c19_probe_accepts_webm_with_audio_and_video(media_files, real_settings):
    result = await probe(media_files["av_webm"], real_settings)
    assert result.has_audio and result.has_video
    assert "webm" in result.format_name or "matroska" in result.format_name


async def test_c19_probe_rejects_video_only_webm(media_files, real_settings):
    with pytest.raises(MAIError) as exc:
        await probe(media_files["video_only_webm"], real_settings)
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA


@pytest.mark.parametrize("name", ["wav", "mp4", "garbage"])
async def test_c19_probe_rejects_non_webm(media_files, real_settings, name):
    with pytest.raises(MAIError) as exc:
        await probe(media_files[name], real_settings)
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA
