from __future__ import annotations

import av
import imagehash

_SENTINEL = 10**9


def phash_file(path: str, n_frames: int) -> list[str]:
    """Perceptual hashes (hex) of n_frames evenly-spaced frames."""
    with av.open(path) as container:
        stream = container.streams.video[0]
        frames = [f.to_image() for f in container.decode(stream)]
    if not frames or n_frames <= 0:
        return []
    last = len(frames) - 1
    if n_frames == 1:
        idxs = [last // 2]
    else:
        idxs = [round(i * last / (n_frames - 1)) for i in range(n_frames)]
    return [str(imagehash.phash(frames[i])) for i in idxs]


def _hamming(x: str, y: str) -> int:
    return bin(int(x, 16) ^ int(y, 16)).count("1")


def min_distance(a: list[str], b: list[str]) -> int:
    """Minimum Hamming distance over all frame pairs; large sentinel if empty."""
    if not a or not b:
        return _SENTINEL
    return min(_hamming(x, y) for x in a for y in b)
