"""Download the MediaPipe models listed in models.lock and verify their hashes."""

import argparse
import shutil
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.landmarks import MODELS_LOCK, ModelError, read_lock, sha256_file

_TIMEOUT_SECONDS = 60


def _urllib_download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response, dest.open("wb") as f:
        shutil.copyfileobj(response, f)


def fetch_models(
    model_dir: Path,
    lock_path: Path = MODELS_LOCK,
    download: Callable[[str, Path], None] = _urllib_download,
) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for entry in read_lock(lock_path):
        target = model_dir / entry.name
        if target.is_file() and sha256_file(target) == entry.sha256:
            continue
        target.unlink(missing_ok=True)  # a stale file must not survive a failed fetch
        part = model_dir / f"{entry.name}.part"
        try:
            download(entry.url, part)
            if sha256_file(part) != entry.sha256:
                raise ModelError(f"{entry.name} does not match models.lock")
            part.replace(target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download MediaPipe models.")
    parser.add_argument("--model-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    model_dir = args.model_dir or Settings().model_dir
    try:
        fetch_models(model_dir)
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: model download failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
