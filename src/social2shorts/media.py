"""Local media probing adapter (``MediaProbe``): ffprobe duration and sampled-frame pHashes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from social2shorts.phash import phash_file


def ffprobe_duration(path: Path) -> float:
    """Container duration in seconds.

    Raises:
        subprocess.CalledProcessError: If ffprobe cannot read the file.
    """
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return float(res.stdout.strip())


class LocalMedia:
    """``MediaProbe`` backed by ffprobe and PyAV."""

    def duration(self, path: Path) -> float:
        """See :func:`ffprobe_duration`."""
        return ffprobe_duration(path)

    def phashes(self, path: Path, n_frames: int) -> list[str]:
        """See :func:`social2shorts.phash.phash_file`."""
        return phash_file(path, n_frames)
