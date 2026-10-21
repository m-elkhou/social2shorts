from __future__ import annotations

from typing import Literal

from social2shorts.models import CatalogEntry


def classify(e: CatalogEntry) -> Literal["original", "licensed"]:
    """Classify a catalog entry based on its audio source.

    Args:
        e: The catalog entry to classify.

    Returns:
        "original" if the entry is original audio, otherwise "licensed".
    """
    return "original" if e.is_original_audio else "licensed"
