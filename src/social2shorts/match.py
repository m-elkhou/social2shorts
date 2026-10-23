"""Matches a published post to its phone original (duration + pHash); cached originals index."""

from __future__ import annotations

from pathlib import Path

from social2shorts.db import Store
from social2shorts.models import CatalogEntry, ItemStatus, MatchResult, Original
from social2shorts.phash import min_distance
from social2shorts.ports import MediaProbe


def index_originals(
    paths: list[Path], store: Store, media: MediaProbe, n_frames: int
) -> list[Original]:
    """Duration + pHashes of each original, computed once per file (cached by path and size)."""
    originals = []
    for path in paths:
        size = path.stat().st_size
        cached = store.cached_original(str(path), size)
        if cached is None:
            cached = (media.duration(path), media.phashes(path, n_frames))
            store.cache_original(str(path), size, *cached)
        duration, phashes = cached
        originals.append(Original(path=str(path), duration=duration, phashes=phashes))
    return originals


def match_one(
    entry: CatalogEntry,
    originals: list[Original],
    ref_phashes: list[str],
    dur_tol: float,
    phash_threshold: int,
) -> MatchResult:
    """Match one post: exactly one candidate within tolerance -> matched; several -> ambiguous
    (a human picks, never a risky automatic choice); none -> pending_original."""
    scored = []
    for o in originals:
        if abs(o.duration - entry.duration) > dur_tol:
            continue
        d = min_distance(ref_phashes, o.phashes)
        if d <= phash_threshold:
            scored.append((d, o))
    scored.sort(key=lambda pair: pair[0])
    if len(scored) == 1:
        d, o = scored[0]
        conf = 1.0 if phash_threshold <= 0 else 1 - d / phash_threshold
        return MatchResult(
            source_id=entry.source_id, original_path=o.path, confidence=conf,
            status=ItemStatus.matched,
        )  # fmt: skip
    if scored:
        return MatchResult(
            source_id=entry.source_id, original_path=None, confidence=0.0,
            status=ItemStatus.ambiguous, candidates=[o.path for _, o in scored],
        )  # fmt: skip
    return MatchResult(
        source_id=entry.source_id, original_path=None, confidence=0.0,
        status=ItemStatus.pending_original,
    )  # fmt: skip
