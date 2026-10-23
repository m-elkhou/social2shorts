"""Renderer adapter: delegates to the montage engine (imported lazily, only when rendering)."""

from __future__ import annotations

from pathlib import Path

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
    ) -> Path:
        """Render ``src`` into a finished Short at ``dst``; return the written path."""
        from social2shorts import montage

        dst.parent.mkdir(parents=True, exist_ok=True)
        result = montage.make_short(
            str(src), str(dst), words=words, settings=settings, hook_text=hook
        )
        return Path(result.path)
