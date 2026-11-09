import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import Settings


def scratch_root(settings: Settings) -> Path:
    return settings.scratch_dir or Path(tempfile.gettempdir()) / "readright-mai"


@contextmanager
def scratch_dir(settings: Settings, request_id: object) -> Iterator[Path]:
    """Per-request working directory, always removed on exit."""
    path = scratch_root(settings) / str(request_id)
    shutil.rmtree(path, ignore_errors=True)  # leftover from a crashed run
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
