"""Single-instance lock: a PID file in the work dir so manual and scheduled runs never overlap."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

STALE_AFTER_S = 6 * 3600  # longer than any run (the scheduled task is capped at 3 h)


class AlreadyRunning(RuntimeError):
    """Another run holds the lock."""


def _take(lock_path: Path) -> int | None:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


@contextmanager
def single_instance(lock_path: Path, stale_after_s: float = STALE_AFTER_S) -> Iterator[None]:
    """Hold ``lock_path`` for the duration of the block.

    A lock older than ``stale_after_s`` (left by a crashed run) is replaced.

    Raises:
        AlreadyRunning: If a live lock is held by another run.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = _take(lock_path)
    if fd is None:
        try:
            age = time.time() - lock_path.stat().st_mtime
            owner = lock_path.read_text(encoding="utf-8").strip() or "?"
        except OSError:  # released between our two calls
            age, owner = stale_after_s, "?"
        if age < stale_after_s:
            raise AlreadyRunning(
                f"another run (pid {owner}) has been working for {age / 60:.0f} min"
            )
        log.warning("Removing a stale lock left by pid %s %.0f h ago", owner, age / 3600)
        lock_path.unlink(missing_ok=True)
        fd = _take(lock_path)
        if fd is None:
            raise AlreadyRunning("another run started at the same moment")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
