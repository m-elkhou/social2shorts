"""Renderer adapter: delegates to the montage engine (imported lazily, only when rendering)."""

from __future__ import annotations

from pathlib import Path

from social2shorts.models import Rendered
from social2shorts.montage.types import MontageSettings, Word


class MontageRenderer:
    """``Renderer`` backed by ``social2shorts.montage.make_short``."""

    def render(
        self,
        src: Path,
        dst: Path,
        *,
        words: list[Word],
        settings: MontageSettings,
        hook: str | None,
    ) -> Rendered:
        """Render ``src`` into a finished Short at ``dst``; words come back in the Short's time."""
        from social2shorts import montage

        dst.parent.mkdir(parents=True, exist_ok=True)
        result = montage.make_short(
            str(src), str(dst), words=words, settings=settings, hook_text=hook
        )
        out_words = montage.remap_words(words, result.plan.cuts)
        return Rendered(path=Path(result.path), words=out_words)
