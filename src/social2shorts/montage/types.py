"""Public data contract of the montage engine.

These types are shared by the pipeline (which feeds transcripts in and gets a
rendered Short out) and the montage internals (analysis → plan → render).
Keep this module dependency-free (pydantic only) so it can be imported cheaply.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Word(BaseModel):
    """One transcribed word with its timing in the *source* clip (seconds)."""

    start: float
    end: float
    text: str


class CaptionStyle(str, Enum):  # noqa: UP042 - keep str() behaviour for callers
    """Burned-in caption look."""

    karaoke = "karaoke"  # word-by-word highlight, 1-3 words on screen, pop-in
    minimal = "minimal"  # plain phrase captions, lower third
    none = "none"


class MontageMode(str, Enum):  # noqa: UP042 - keep str() behaviour for callers
    """How aggressively the engine edits the source."""

    polish = "polish"  # already-edited reel: keep the cut, only reframe/grade/captions/loudness
    highlight = "highlight"  # raw footage: pick the best moments, hook first, re-cut


class MontageSettings(BaseModel):
    """Tunable knobs of the montage engine (``[montage]`` table in config.toml)."""

    mode: MontageMode = MontageMode.highlight
    width: int = 1080
    height: int = 1920
    fps: int = 30
    target_duration_s: float = Field(35.0, gt=0, le=180)  # Shorts must stay <= 180 s
    min_duration_s: float = Field(12.0, gt=0)
    hook_s: float = 2.0  # best moment pulled to the very front (0 disables)
    reframe: Literal["smart", "blur", "crop", "pad"] = "smart"
    captions: CaptionStyle = CaptionStyle.karaoke
    caption_font: str = "Arial Black"
    caption_size: int = 88
    caption_highlight: str = "&H0000E5FF"  # ASS BGR: punchy yellow
    hook_text: bool = True  # overlay the LLM hook line during the first seconds
    loudness_lufs: float = -14.0  # YouTube playback reference
    color_grade: bool = True
    punch_in: bool = True  # subtle zoom on segment starts for energy
    progress_bar: bool = True
    fade_s: float = 0.2
    music_dir: str | None = None  # folder of royalty-free tracks (never licensed music)
    music_volume_db: float = -20.0
    crf: int = 18
    preset: str = "slow"


class MediaInfo(BaseModel):
    """Technical facts about a source clip, from ffprobe."""

    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    rotation: int = 0
    pix_fmt: str = ""  # e.g. yuv420p10le for 10-bit phone footage
    color_transfer: str = ""  # arib-std-b67 (HLG) / smpte2084 (PQ) mean HDR
    color_primaries: str = ""
    color_space: str = ""

    @property
    def is_hdr(self) -> bool:
        """True for HLG or PQ sources, which must be tonemapped to SDR BT.709."""
        return self.color_transfer in ("arib-std-b67", "smpte2084")


class Cut(BaseModel):
    """A source interval kept in the final edit."""

    start: float
    end: float
    speed: float = 1.0
    zoom: float = 1.0  # end-of-segment zoom factor for punch-in (1.0 = none)
    reason: str = ""
    crop_x: float | None = None  # per-cut stationary crop centre (0..1); None = plan crop_x

    @property
    def out_duration(self) -> float:
        return (self.end - self.start) / self.speed


class Variation(BaseModel):
    """Deterministic per-video style differences so a batch of Shorts doesn't look templated."""

    punch_zoom: float = 1.08  # end-of-segment zoom used on punch-in cuts
    rhythm: float = 1.0  # multiplier on segment lengths (<1 = snappier cutting)
    accent: str = "&H0000E5FF"  # ASS &HAABBGGRR colour of the active caption word
    caption_y: float = 0.66  # caption baseline, fraction of frame height
    hook_y: float = 0.16  # hook title top edge, fraction of frame height


class EditPlan(BaseModel):
    """Everything needed to render: which cuts, in which order, and how to frame them."""

    source: MediaInfo
    cuts: list[Cut]
    crop_x: float | None = None  # normalized horizontal centre of the 9:16 window (0..1)
    music: str | None = None
    reframe: Literal["crop", "blur", "pad"] | None = None  # resolved mode; None = from settings
    variation: Variation = Field(default_factory=Variation)
    loop_s: float = 0.0  # crossfade the tail into the opening frame for seamless replays

    @property
    def duration(self) -> float:
        return sum(c.out_duration for c in self.cuts)


class RenderResult(BaseModel):
    """What the engine produced."""

    path: str
    duration: float
    plan: EditPlan
