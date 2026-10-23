"""Execute an EditPlan: write captions, run one ffmpeg pass, validate the output."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

from .captions import build_ass
from .errors import MontageError
from .filters import build_command
from .planner import remap_words
from .probe import probe
from .types import CaptionStyle, EditPlan, MontageSettings, RenderResult, Word

log = logging.getLogger(__name__)

STDERR_TAIL = 1500  # characters of ffmpeg stderr kept in errors
DURATION_TOLERANCE_S = 0.5


def render(
    plan: EditPlan,
    dst: str,
    words: Sequence[Word] = (),
    settings: MontageSettings | None = None,
    hook_text: str | None = None,
) -> RenderResult:
    """Render ``plan`` to ``dst``.

    Args:
        plan: The edit to render.
        dst: Output .mp4 path.
        words: Transcript in SOURCE time (remapped through the plan here).
        settings: Montage settings (defaults if omitted).
        hook_text: Title for the opening seconds (used when ``settings.hook_text``).

    Returns:
        The :class:`RenderResult` with the probed output duration.

    Raises:
        MontageError: If ffmpeg fails or the output is not a valid 9:16 Short with audio.
    """
    settings = settings or MontageSettings()
    hook = hook_text if settings.hook_text else None
    out_words = remap_words(words, plan.cuts)
    want_ass = bool(hook) or (settings.captions != CaptionStyle.none and out_words)
    tmpdir = tempfile.mkdtemp(prefix="s2s_montage_")
    try:
        ass_path = None
        if want_ass:
            ass_path = os.path.join(tmpdir, "captions.ass")
            doc = build_ass(
                out_words,
                settings,
                duration=plan.duration,
                hook_text=hook,
                variation=plan.variation,
            )
            with open(ass_path, "w", encoding="utf-8") as fh:
                fh.write(doc)
        cmd = build_command(plan, dst, settings, ass_path)
        log.info(
            "rendering %s → %s (%d cuts, %.1fs)",
            plan.source.path,
            dst,
            len(plan.cuts),
            plan.duration,
        )
        log.debug("ffmpeg %s", " ".join(cmd[1:]))
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise MontageError(
                f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-STDERR_TAIL:]}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return _validate(plan, dst, settings)


def _validate(plan: EditPlan, dst: str, settings: MontageSettings) -> RenderResult:
    info = probe(dst)
    if (info.width, info.height) != (settings.width, settings.height):
        raise MontageError(
            f"output is {info.width}x{info.height}, expected {settings.width}x{settings.height}"
        )
    if not info.has_audio:
        raise MontageError("output has no audio track")
    if abs(info.duration - plan.duration) > max(DURATION_TOLERANCE_S, 0.03 * plan.duration):
        log.warning("output duration %.2fs differs from plan %.2fs", info.duration, plan.duration)
    return RenderResult(path=dst, duration=info.duration, plan=plan)
