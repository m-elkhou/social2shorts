"""Deterministic per-video "humanizing" variation, seeded by a hash of the source file content."""

from __future__ import annotations

import hashlib
import os
import random

from .types import MontageSettings, Variation

SEED_HEAD_BYTES = 1 << 20  # hash only the first 1 MB: cheap, and unique enough for raw clips

# Curated caption accents (ASS &HAABBGGRR): yellow, lime, cyan, orange, hot pink, white-gold.
ACCENT_PALETTE = (
    "&H0000E5FF",
    "&H0033FF99",
    "&H00FFE000",
    "&H00169BFF",
    "&H00B469FF",
    "&H0080F0FF",
)
PUNCH_RANGE = (1.05, 1.10)  # end-of-segment zoom factor
RHYTHM_RANGE = (0.85, 1.15)  # multiplier on segment lengths (faster / slower cutting)
CAPTION_Y_RANGE = (0.63, 0.69)  # caption baseline as a fraction of frame height
HOOK_Y_RANGE = (0.14, 0.20)  # hook title top edge as a fraction of frame height (inside safe box)


def seed_for(path: str) -> int:
    """Stable 64-bit seed from the file content (size + hash of the first 1 MB).

    Two clips with the same name get different styles; a renamed copy keeps its style.
    Falls back to the file name when the file can't be read (e.g. in unit tests).
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            h.update(str(os.fstat(fh.fileno()).st_size).encode())
            h.update(fh.read(SEED_HEAD_BYTES))
    except OSError:
        h.update(os.path.basename(path).encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "big")


def make_variation(path: str, settings: MontageSettings) -> Variation:
    """Draw the variation for ``path``.

    An explicitly configured ``caption_highlight`` wins over the palette.
    """
    rng = random.Random(seed_for(path))
    accent = rng.choice(ACCENT_PALETTE)
    if "caption_highlight" in settings.model_fields_set:
        accent = settings.caption_highlight
    return Variation(
        punch_zoom=round(rng.uniform(*PUNCH_RANGE), 3),
        rhythm=round(rng.uniform(*RHYTHM_RANGE), 3),
        accent=accent,
        caption_y=round(rng.uniform(*CAPTION_Y_RANGE), 3),
        hook_y=round(rng.uniform(*HOOK_Y_RANGE), 3),
    )
