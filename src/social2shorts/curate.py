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
    """Deterministic engagement score.

    score = 1.0 * log1p(views)
          + 2.0 * min((likes + 2*comments) / max(views, 1), 1.0)   # engagement ratio
          + 0.5 if 15 <= duration <= 60 else 0                      # sweet-spot bonus
    """
    views = max(e.view_count, 0)
    engagement = (max(e.like_count, 0) + 2 * max(e.comment_count, 0)) / max(views, 1)
    bonus = W_SWEET_SPOT if SWEET_SPOT_S[0] <= e.duration <= SWEET_SPOT_S[1] else 0.0
    return W_VIEWS * math.log1p(views) + W_ENGAGEMENT * min(engagement, 1.0) + bonus


def is_eligible(e: CatalogEntry, min_duration_s: float, exclude_no_duration: bool = True) -> bool:
    if exclude_no_duration and e.duration <= 0:
        return False
    return e.duration >= min_duration_s


def curate(entries: list[CatalogEntry], settings: Settings) -> list[CatalogEntry]:
    kept = [e for e in entries if is_eligible(e, settings.min_duration_s)]
    return sorted(kept, key=score, reverse=True)
