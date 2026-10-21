"""
Curation module.

This module provides scoring and eligibility logic to select the best videos
from the catalog for republishing based on engagement metrics.
"""
from __future__ import annotations

import math

from social2shorts.config import Settings
from social2shorts.models import CatalogEntry

SWEET_SPOT_S = (15.0, 60.0)

# Weights: views dominate; engagement ratio and sweet-spot duration are minor.
W_VIEWS = 1.0
W_ENGAGEMENT = 2.0
W_SWEET_SPOT = 0.5


def score(e: CatalogEntry) -> float:
    """
    Calculate a deterministic engagement score for a catalog entry.

    The score is based on a combination of view count (logarithmic),
    engagement ratio (likes and comments relative to views), and a
    duration bonus for videos in the "sweet spot".

    Args:
        e (CatalogEntry): The catalog entry to score.

    Returns:
        float: The calculated engagement score.
    """
    views = max(e.view_count, 0)
    engagement = (max(e.like_count, 0) + 2 * max(e.comment_count, 0)) / max(views, 1)
    bonus = W_SWEET_SPOT if SWEET_SPOT_S[0] <= e.duration <= SWEET_SPOT_S[1] else 0.0
    return W_VIEWS * math.log1p(views) + W_ENGAGEMENT * min(engagement, 1.0) + bonus


def is_eligible(e: CatalogEntry, min_duration_s: float, exclude_no_duration: bool = True) -> bool:
    """
    Check if a catalog entry is eligible for curation.

    Args:
        e (CatalogEntry): The catalog entry to check.
        min_duration_s (float): Minimum required duration in seconds.
        exclude_no_duration (bool): Whether to exclude videos with zero or negative duration.

    Returns:
        bool: True if the entry is eligible, False otherwise.
    """
    if exclude_no_duration and e.duration <= 0:
        return False
    return e.duration >= min_duration_s


def curate(entries: list[CatalogEntry], settings: Settings) -> list[CatalogEntry]:
    """
    Filter and sort catalog entries to produce a curated shortlist.

    Args:
        entries (list[CatalogEntry]): The list of entries to curate.
        settings (Settings): Application settings for eligibility criteria.

    Returns:
        list[CatalogEntry]: The curated shortlist, sorted by score in descending order.
    """
    kept = [e for e in entries if is_eligible(e, settings.min_duration_s)]
    return sorted(kept, key=score, reverse=True)
