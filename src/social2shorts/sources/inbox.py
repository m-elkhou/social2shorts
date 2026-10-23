"""Inbox adapter: finds raw, never-published videos in a folder and keys them by content hash."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from social2shorts.db import Store
from social2shorts.sources import video_files

log = logging.getLogger(__name__)

CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class RawVideo:
    """An inbox file with its content hash."""

    path: Path
    sha256: str

    @property
    def key(self) -> str:
        """Stable item key; identical content at two paths gets the same key."""
        return f"raw-{self.sha256[:16]}"


def sha256_file(path: Path) -> str:
    """sha256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def scan_inbox(inbox: Path, store: Store) -> list[RawVideo]:
    """Hash every video in ``inbox`` (hash cached by path+size+mtime) and drop duplicates."""
    found: dict[str, RawVideo] = {}
    for path in video_files(inbox):
        stat = path.stat()
        sha = store.cached_hash(str(path), stat.st_size, stat.st_mtime_ns)
        if sha is None:
            sha = sha256_file(path)
            store.cache_hash(str(path), stat.st_size, stat.st_mtime_ns, sha)
        video = RawVideo(path, sha)
        if video.key in found:
            log.info("Skipping %s: same content as %s", path.name, found[video.key].path.name)
            continue
        found[video.key] = video
    return list(found.values())
