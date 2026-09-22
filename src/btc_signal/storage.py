"""Local serialisation and atomic writes; immutable artifacts cannot be replaced."""
from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .core import SignalError, canonical


@contextmanager
def lock(root: Path, name: str):
    folder = root / ".locks"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{name}.lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path: Path, value: dict, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = canonical(value) + b"\n"
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        if immutable:
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise SignalError("immutable_artifact_exists") from exc
        else:
            os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
