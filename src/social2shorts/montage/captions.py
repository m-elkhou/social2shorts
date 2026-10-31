"""Build the ASS subtitle file (karaoke/minimal captions and the hook title card) and the SRT
subtitle track uploaded next to the video.

Arabic/RTL text is written in logical order and shaped by libass (fribidi/HarfBuzz) at burn-in
time. caption_font_fallback: if ``caption_font`` lacks Arabic glyphs, fontconfig substitutes a
font that has them (e.g. Noto Sans Arabic) — install one on render machines for best results.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .types import CaptionStyle, MontageSettings, Variation, Word

HOOK_SECONDS = 2.5  # how long the hook title stays on screen
CUE_MAX_WORDS = 3  # karaoke: words on screen at once
CUE_MAX_CHARS = 16  # karaoke: break earlier for long words
PHRASE_MAX_WORDS = 7  # minimal: words per phrase
PHRASE_MAX_CHARS = 34
CUE_GAP_S = 0.45  # a pause this long starts a new cue
SUB_MAX_WORDS = 16  # subtitle track (SRT): words per cue
SUB_MAX_CHARS = 84  # subtitle track: two lines of 42 (the player wraps them)
SUB_MIN_S = 1.0  # subtitle track: shortest cue, unless the next one starts sooner
REF_CANVAS = (1080, 1920)  # layout constants below are specified on this canvas
SAFE_BOX = (900, 1400)  # centred box clear of Shorts UI on every platform (scaled to output)
RIGHT_UI_MARGIN = 0.14  # right margin: Shorts action buttons live there (stricter than the box)
POP = r"{\fscx80\fscy80\t(0,120,\fscx100\fscy100)}"  # cue entrance "pop"
WHITE = "&H00FFFFFF"


def ass_time(t: float) -> str:
    """Format seconds as ASS ``H:MM:SS.cc``."""
    cs = max(round(t * 100), 0)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def escape_ass(text: str) -> str:
    """Neutralise ASS override syntax and line breaks in user/transcript text."""
    return (
        text.replace("\\", "\uff3c")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _bgr(colour: str) -> str:
    """``&HAABBGGRR`` → ``&HBBGGRR&`` for inline ``\\c`` overrides."""
    return f"&H{colour.strip('&Hh')[-6:].upper()}&"


def _group(words: Sequence[Word], max_words: int, max_chars: int) -> list[list[Word]]:
    """Split words into cues on pauses, sentence punctuation and size limits."""
    cues: list[list[Word]] = []
    for w in words:
        cur = cues[-1] if cues else None
        if (
            cur is None
            or len(cur) >= max_words
            or w.start - cur[-1].end > CUE_GAP_S
            or sum(len(x.text) + 1 for x in cur) + len(w.text) > max_chars
            or cur[-1].text.rstrip().endswith((".", "!", "?", ",", "؟", "،"))
        ):
            cues.append([w])
        else:
            cur.append(w)
    return cues


def _dialogue(start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def _karaoke(words: Sequence[Word], accent: str) -> list[str]:
    """1-3 uppercase words per cue; the word being spoken is coloured; cues pop in."""
    events = []
    for cue in _group(words, CUE_MAX_WORDS, CUE_MAX_CHARS):
        texts = [escape_ass(w.text).upper() for w in cue]
        for k, w in enumerate(cue):
            end = cue[k + 1].start if k + 1 < len(cue) else w.end
            parts = [
                f"{{\\c{_bgr(accent)}}}{t}{{\\c{_bgr(WHITE)}}}" if j == k else t
                for j, t in enumerate(texts)
            ]
            events.append(
                _dialogue(
                    w.start,
                    max(end, w.start + 0.05),
                    "Caption",
                    (POP if k == 0 else "") + " ".join(parts),
                )
            )
    return events


def _minimal(words: Sequence[Word], accent: str) -> list[str]:
    """Plain phrase captions."""
    return [
        _dialogue(cue[0].start, cue[-1].end, "Phrase", " ".join(escape_ass(w.text) for w in cue))
        for cue in _group(words, PHRASE_MAX_WORDS, PHRASE_MAX_CHARS)
    ]


CAPTION_STYLES: dict[CaptionStyle, Callable[[Sequence[Word], str], list[str]]] = {
    CaptionStyle.karaoke: _karaoke,
    CaptionStyle.minimal: _minimal,
    CaptionStyle.none: lambda words, accent: [],
}


def srt_time(t: float) -> str:
    """Format seconds as SubRip ``HH:MM:SS,mmm``."""
    ms = max(round(t * 1000), 0)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def build_srt(words: Sequence[Word]) -> str:
    """SubRip file for a platform subtitle track (viewers can turn it on; the owner can edit it).

    Words must already be in OUTPUT time. Cues are phrases split on pauses, sentence punctuation
    and :data:`SUB_MAX_CHARS`; each stays at least :data:`SUB_MIN_S`, never past the next cue.
    Empty when there are no words.
    """
    cues = _group([w for w in words if w.text.strip()], SUB_MAX_WORDS, SUB_MAX_CHARS)
    blocks = []
    for i, cue in enumerate(cues):
        start = cue[0].start
        end = max(cue[-1].end, start + SUB_MIN_S)
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0].start)
        text = " ".join(" ".join(w.text.split()) for w in cue)
        blocks.append(f"{i + 1}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
    return "\n".join(blocks)


def safe_box(width: int, height: int) -> tuple[int, int, int, int]:
    """Pixel ``(left, top, right, bottom)`` of :data:`SAFE_BOX` scaled to the output size."""
    mx = (REF_CANVAS[0] - SAFE_BOX[0]) / 2 * width / REF_CANVAS[0]
    my = (REF_CANVAS[1] - SAFE_BOX[1]) / 2 * height / REF_CANVAS[1]
    return round(mx), round(my), round(width - mx), round(height - my)


def _header(settings: MontageSettings, var: Variation) -> str:
    w, h, size = settings.width, settings.height, settings.caption_size
    scale = w / REF_CANVAS[0]  # caption_size is specified for a 1080-wide frame
    cap, phrase, hook = int(size * scale), int(size * 0.62 * scale), int(size * scale)
    outline, shadow = max(2, round(cap * 0.09)), max(1, round(cap * 0.04))
    hook_pad = max(4, hook // 4)  # opaque box padding around the hook text
    left, top, right, bottom = safe_box(w, h)
    ml, mr = left + hook_pad, max(w - right, int(w * RIGHT_UI_MARGIN)) + hook_pad
    mv_cap = max(int(h * (1 - var.caption_y)), h - bottom + outline)  # baseline at caption_y
    mv_hook = max(int(h * var.hook_y), top + hook_pad)
    font = settings.caption_font
    fmt = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {w}",
            f"PlayResY: {h}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            fmt,
            (
                f"Style: Caption,{font},{cap},{WHITE},{WHITE},&H00000000,&H96000000,"
                f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{ml},{mr},{mv_cap},1"
            ),
            (
                f"Style: Phrase,{font},{phrase},{WHITE},{WHITE},&H00000000,&H96000000,"
                f"-1,0,0,0,100,100,0,0,1,{max(2, outline // 2)},{shadow},2,{ml},{mr},{mv_cap},1"
            ),
            (
                f"Style: Hook,{font},{hook},{WHITE},{WHITE},&H40000000,&H00000000,"
                f"-1,0,0,0,100,100,0,0,3,{hook_pad},0,8,{ml},{mr},{mv_hook},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )


def build_ass(
    words: Sequence[Word],
    settings: MontageSettings,
    *,
    duration: float,
    hook_text: str | None = None,
    variation: Variation | None = None,
) -> str:
    """Render an ASS document.

    Args:
        words: Words already in OUTPUT time.
        settings: Caption style, font, size and frame size.
        duration: Output duration; events are clipped to it.
        hook_text: Optional title shown in the top third for the first seconds.
        variation: Per-video accent colour and vertical positions.

    Returns:
        The ASS file content.
    """
    var = variation or Variation(accent=settings.caption_highlight)
    kept = [w for w in words if w.start < duration and w.text.strip()]
    kept = [w.model_copy(update={"end": min(w.end, duration)}) for w in kept]
    events = CAPTION_STYLES[settings.captions](kept, var.accent)
    if hook_text and hook_text.strip():
        pop = r"{\fad(150,300)\fscx70\fscy70\t(0,220,\fscx100\fscy100)}"
        events.insert(
            0, _dialogue(0.0, min(HOOK_SECONDS, duration), "Hook", pop + escape_ass(hook_text))
        )
    return _header(settings, var) + "\n" + "\n".join(events) + "\n"
