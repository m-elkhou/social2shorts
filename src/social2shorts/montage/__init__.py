"""AI montage engine: turns a raw or edited clip into a polished 9:16 YouTube Short.

Pipeline: :func:`probe` → :func:`analyze` → :func:`plan_edit` → :func:`render`.
"""

from __future__ import annotations

from collections.abc import Sequence

from .analyze import Analysis, analyze
from .captions import build_ass
from .errors import MontageError
from .filters import build_command, build_filter_graph
from .planner import pick_music, plan_edit, remap_words
from .probe import probe
from .render import render
from .types import (
    CaptionStyle,
    Cut,
    EditPlan,
    MediaInfo,
    MontageMode,
    MontageSettings,
    RenderResult,
    Variation,
    Word,
)

__all__ = [
    "Analysis",
    "CaptionStyle",
    "Cut",
    "EditPlan",
    "MediaInfo",
    "MontageError",
    "MontageMode",
    "MontageSettings",
    "RenderResult",
    "Variation",
    "Word",
    "analyze",
    "build_ass",
    "build_command",
    "build_filter_graph",
    "make_short",
    "plan_edit",
    "probe",
    "remap_words",
    "render",
]


def make_short(
    src: str,
    dst: str,
    *,
    words: Sequence[Word],
    settings: MontageSettings,
    hook_text: str | None = None,
) -> RenderResult:
    """Turn ``src`` into a finished Short at ``dst``.

    Args:
        src: Source clip.
        dst: Output .mp4 path.
        words: Transcript in source time (may be empty).
        settings: Montage settings.
        hook_text: Optional title card for the first seconds.

    Returns:
        The render result, including the plan that was used.

    Raises:
        MontageError: On any probe/analysis/render failure.
    """
    info = probe(src)
    analysis = analyze(src, info, words)
    plan = plan_edit(info, analysis, words, settings, music=pick_music(settings.music_dir, src))
    return render(plan, dst, words, settings, hook_text)
