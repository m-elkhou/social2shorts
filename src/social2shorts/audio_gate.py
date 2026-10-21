from __future__ import annotations

from typing import Literal

from social2shorts.models import CatalogEntry


def classify(e: CatalogEntry) -> Literal["original", "licensed"]:
    return "original" if e.is_original_audio else "licensed"
