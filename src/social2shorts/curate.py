"""Curation: eligibility filters, engagement score and cross-platform dedupe of catalog posts."""

from __future__ import annotations

import math

from social2shorts.db import catalog_fingerprint, normalize_caption
from social2shorts.models import CatalogEntry, Platform

SWEET_SPOT_S = (15.0, 60.0)

# Views dominate; engagement ratio and sweet-spot duration are minor.
W_VIEWS = 1.0
W_ENGAGEMENT = 2.0
W_SWEET_SPOT = 0.5


def score(e: CatalogEntry) -> float:
    """Deterministic engagement score (log views + capped engagement ratio + duration bonus)."""
    views = max(e.view_count, 0)
    engagement = (max(e.like_count, 0) + 2 * max(e.comment_count, 0)) / max(views, 1)
    bonus = W_SWEET_SPOT if SWEET_SPOT_S[0] <= e.duration <= SWEET_SPOT_S[1] else 0.0
    return W_VIEWS * math.log1p(views) + W_ENGAGEMENT * min(engagement, 1.0) + bonus


def is_eligible(e: CatalogEntry, min_duration_s: float, exclude_no_duration: bool = True) -> bool:
    """Exclude micro-clips and photo slideshows (no duration)."""
    if exclude_no_duration and e.duration <= 0:
        return False
    return e.duration >= min_duration_s


def dedupe(entries: list[CatalogEntry], prefer: Platform) -> list[CatalogEntry]:
    """Soft cross-platform dedupe: same caption + duration on two platforms -> keep ``prefer``'s.

    Only a hint to save work: posts on the same platform, or with no words in their caption
    (emoji-only), are never merged. The real guard is the source-file hash at upload time.
    """
    kept: list[CatalogEntry] = []
    index: dict[str, int] = {}
    for e in entries:
        fp = catalog_fingerprint(e) if normalize_caption(e.caption) else None
        i = index.get(fp) if fp else None
        if i is None or kept[i].platform is e.platform:
            if fp and i is None:
                index[fp] = len(kept)
            kept.append(e)
        elif e.platform is prefer:
            kept[i] = e
    return kept


def curate(
    entries: list[CatalogEntry], min_duration_s: float, prefer: Platform = Platform.tiktok
) -> list[CatalogEntry]:
    """Eligible, deduplicated entries, best score first."""
    kept = [e for e in dedupe(entries, prefer) if is_eligible(e, min_duration_s)]
    return sorted(kept, key=score, reverse=True)
