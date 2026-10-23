"""Inbox adapter: finds raw, never-published videos in a folder and keys them by content hash."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from social2shorts.db import Store
from social2shorts.media import content_hash
from social2shorts.sources import video_files

log = logging.getLogger(__name__)

SETTLE_S = 60.0  # files modified more recently may still be copying in


@dataclass(frozen=True)
class RawVideo:
    """An inbox file with its content hash."""

    path: Path
    sha256: str

    @property
    def key(self) -> str:
        """Stable item key; identical content at two paths gets the same key."""
        return f"raw-{self.sha256[:16]}"


def scan_inbox(inbox: Path, store: Store, now: float | None = None) -> list[RawVideo]:
    """Hash every settled, readable video in ``inbox`` and drop content duplicates.

    Files modified in the last ``SETTLE_S`` seconds (still being copied) and unreadable files
    are skipped for this run, never fatal.
    """
    now = time.time() if now is None else now
    found: dict[str, RawVideo] = {}
    for path in video_files(inbox):
        try:
            if now - path.stat().st_mtime < SETTLE_S:
                log.info("Skipping %s for now: it was modified less than a minute ago", path.name)
                continue
            video = RawVideo(path, content_hash(path, store))
        except OSError as exc:  # PermissionError while another program holds it, vanished file
            log.warning("Skipping %s: cannot read it (%s)", path.name, exc)
            continue
        if video.key in found:
            log.info("Skipping %s: same content as %s", path.name, found[video.key].path.name)
            continue
        found[video.key] = video
    return list(found.values())
