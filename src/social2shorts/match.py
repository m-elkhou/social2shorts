"""
Matching module.

This module implements the logic for matching social media videos to their
high-quality original versions using duration and pHash distances.
"""
from __future__ import annotations

from social2shorts.models import CatalogEntry, ItemStatus, MatchResult, Original
from social2shorts.phash import min_distance


def match_one(
    entry: CatalogEntry,
    originals: list[Original],
    ref_phashes: list[str],
    dur_tol: float,
    phash_threshold: int,
) -> MatchResult:
    """
    Match a single catalog entry against a list of original videos.

    Args:
        entry (CatalogEntry): The video to match.
        originals (list[Original]): The list of available original videos.
        ref_phashes (list[str]): The pHashes of the reference video.
        dur_tol (float): Duration tolerance in seconds.
        phash_threshold (int): Maximum Hamming distance for a match.

    Returns:
        MatchResult: The result of the match, indicating if it's matched,
            ambiguous, or pending.
    """
    scored = []
    for o in originals:
        if abs(o.duration - entry.duration) > dur_tol:
            continue
        d = min_distance(ref_phashes, o.phashes)
        if d <= phash_threshold:
            scored.append((d, o))
    if len(scored) == 1:
        d, o = scored[0]
        conf = 1.0 if phash_threshold <= 0 else 1 - d / phash_threshold
        return MatchResult(source_id=entry.source_id, original_path=o.path,
                           confidence=conf, status=ItemStatus.matched)
    if len(scored) >= 2:
        return MatchResult(source_id=entry.source_id, original_path=None,
                           confidence=0.0, status=ItemStatus.ambiguous)
    return MatchResult(source_id=entry.source_id, original_path=None,
                       confidence=0.0, status=ItemStatus.pending_original)
