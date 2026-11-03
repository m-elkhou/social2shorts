"""A handful of JPEG frames of a clip, so the metadata model sees what the video shows.

Titles written from the caption and the transcript alone invented "solo" rides over a group of
riders and "motorway" laps over country roads (2026-11-03): the model must look at the picture.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from social2shorts.media import ffprobe_duration

log = logging.getLogger(__name__)

FRAME_COUNT = 6
FRAME_HEIGHT = 640  # px: enough to count riders and read overlays, small enough to send inline


def no_frames(path: Path) -> list[bytes]:
    """Frame sampler that sees nothing (tests, and runs without a vision model)."""
    return []


def sample_frames(path: Path, count: int = FRAME_COUNT, height: int = FRAME_HEIGHT) -> list[bytes]:
    """``count`` JPEG frames spread evenly over ``path``, oldest first.

    A frame that cannot be decoded is skipped; an unreadable clip gives an empty list (the
    caller then writes the metadata from the caption and transcript only).
    """
    try:
        duration = ffprobe_duration(path)
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        log.warning("Could not read the duration of %s (%s): no frames for the metadata", path, exc)
        return []
    frames: list[bytes] = []
    for i in range(count):
        at = duration * (i + 0.5) / count
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{at:.2f}", "-i", str(path),
               "-frames:v", "1", "-vf", f"scale=-2:{height}", "-q:v", "4",
               "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]  # fmt: skip
        try:
            data = subprocess.run(cmd, capture_output=True, check=True).stdout
        except (subprocess.CalledProcessError, OSError) as exc:
            log.info("No frame at %.1fs of %s (%s)", at, path, exc)
            continue
        if data:
            frames.append(data)
    return frames
