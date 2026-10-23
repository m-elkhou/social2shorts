"""Perceptual hashes of a few sampled video frames, and the distance between two hash sets.

Frames are turned upright first (display-matrix rotation), so a portrait phone original stored
as rotated landscape matches its upright social-media copy.
"""

from __future__ import annotations

from pathlib import Path

import av
import imagehash

_SENTINEL = 10**9
_FALLBACK_STRIDE = 30  # frames between samples when the duration is unknown


def sample_times(duration: float, n_frames: int) -> list[float]:
    """``n_frames`` timestamps evenly spread inside ``(0, duration)`` (frame-centred)."""
    return [duration * (i + 0.5) / n_frames for i in range(n_frames)]


def phash_file(path: str | Path, n_frames: int) -> list[str]:
    """Hex pHashes of ``n_frames`` frames, found by seeking (never decodes the whole file)."""
    if n_frames <= 0:
        return []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        duration = _duration(container, stream)
        if duration <= 0:
            return _hash_sequential(container, stream, n_frames)
        hashes = []
        for t in sample_times(duration, n_frames):
            frame = _frame_at(container, stream, t)
            if frame is not None:
                hashes.append(_hash(frame))
        return hashes


def _duration(container: av.container.InputContainer, stream: av.video.stream.VideoStream) -> float:
    if stream.duration and stream.time_base:
        return float(stream.duration * stream.time_base)
    if container.duration:
        return container.duration / av.time_base
    return 0.0


def _frame_at(
    container: av.container.InputContainer, stream: av.video.stream.VideoStream, t: float
) -> av.VideoFrame | None:
    """First decoded frame at or after ``t`` seconds (seek to the previous keyframe first)."""
    container.seek(int(t / stream.time_base), stream=stream, backward=True)
    last = None
    for frame in container.decode(stream):
        last = frame
        if frame.time is None or frame.time >= t:
            return frame
    return last


def _hash_sequential(
    container: av.container.InputContainer, stream: av.video.stream.VideoStream, n_frames: int
) -> list[str]:
    hashes = []
    for i, frame in enumerate(container.decode(stream)):
        if i % _FALLBACK_STRIDE == 0:
            hashes.append(_hash(frame))
            if len(hashes) == n_frames:
                break
    return hashes


def _hash(frame: av.VideoFrame) -> str:
    """pHash of the frame as displayed (rotation applied; PIL rotates counter-clockwise)."""
    image = frame.to_image()
    if frame.rotation:
        image = image.rotate(frame.rotation, expand=True)
    return str(imagehash.phash(image))


def _hamming(x: str, y: str) -> int:
    return bin(int(x, 16) ^ int(y, 16)).count("1")


def min_distance(a: list[str], b: list[str]) -> int:
    """Smallest Hamming distance over all frame pairs (huge sentinel if a side is empty)."""
    if not a or not b:
        return _SENTINEL
    return min(_hamming(x, y) for x in a for y in b)
