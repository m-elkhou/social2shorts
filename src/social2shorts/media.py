"""Local media facts: ffprobe duration and sampled pHashes (``MediaProbe``), cached file hashes."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from social2shorts.db import Store
from social2shorts.models import AudioLevel
from social2shorts.phash import phash_file

CHUNK = 1 << 20  # 1 MiB


def ffprobe_duration(path: Path) -> float:
    """Container duration in seconds.

    Raises:
        subprocess.CalledProcessError: If ffprobe cannot read the file.
        ValueError: If ffprobe reports no duration.
    """
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )  # fmt: skip
    return float(res.stdout.strip())


_VOLUME = re.compile(r"(mean|max)_volume: (-?[\d.]+|-inf) dB")


def audio_level(path: Path) -> AudioLevel:
    """Mean and peak volume of ``path``'s sound (ffmpeg ``volumedetect``); no audio track is
    reported as such.

    Raises:
        subprocess.CalledProcessError: If ffprobe or ffmpeg cannot read the file.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )  # fmt: skip
    if not probe.stdout.strip():
        return AudioLevel(has_audio=False)
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-vn",
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )  # fmt: skip
    found = {kind: float(v) for kind, v in _VOLUME.findall(res.stderr)}
    return AudioLevel(has_audio=True, mean_db=found.get("mean"), max_db=found.get("max"))


def sha256_file(path: Path) -> str:
    """sha256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def content_hash(path: Path, store: Store) -> str:
    """sha256 of a file, cached in the DB while its size and mtime are unchanged."""
    stat = path.stat()
    sha = store.cached_hash(str(path), stat.st_size, stat.st_mtime_ns)
    if sha is None:
        sha = sha256_file(path)
        store.cache_hash(str(path), stat.st_size, stat.st_mtime_ns, sha)
    return sha


class LocalMedia:
    """``MediaProbe`` backed by ffprobe and PyAV."""

    def duration(self, path: Path) -> float:
        """See :func:`ffprobe_duration`."""
        return ffprobe_duration(path)

    def phashes(self, path: Path, n_frames: int) -> list[str]:
        """See :func:`social2shorts.phash.phash_file`."""
        return phash_file(path, n_frames)

    def audio_level(self, path: Path) -> AudioLevel:
        """See :func:`audio_level`."""
        return audio_level(path)
