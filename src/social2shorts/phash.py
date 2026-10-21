"""
Perceptual hashing module.

This module provides functionality to generate pHashes for videos and
calculate the Hamming distance between them for visual similarity matching.
"""
from __future__ import annotations

import av
import imagehash

_SENTINEL = 10**9


def phash_file(path: str, n_frames: int) -> list[str]:
    """
    Generate perceptual hashes (hex) of n_frames evenly-spaced frames.

    Args:
        path (str): Path to the video file.
        n_frames (int): Number of frames to sample and hash.

    Returns:
        list[str]: A list of pHash strings. Empty list if no frames are found.
    """
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
    """
    Calculate the Hamming distance between two hex pHashes.

    Args:
        x (str): First pHash.
        y (str): Second pHash.

    Returns:
        int: The Hamming distance.
    """
    return bin(int(x, 16) ^ int(y, 16)).count("1")


def min_distance(a: list[str], b: list[str]) -> int:
    """
    Find the minimum Hamming distance over all frame pairs.

    Args:
        a (list[str]): First list of pHashes.
        b (list[str]): Second list of pHashes.

    Returns:
        int: The minimum distance found, or a large sentinel if either list is empty.
    """
    if not a or not b:
        return _SENTINEL
    return min(_hamming(x, y) for x in a for y in b)
