"""Enrichment: burned-caption support (faster-whisper) and LLM-written YouTube metadata.

Only PUBLIC content (caption + transcript of the owner's own public video) is
ever sent to the LLM.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

from social2shorts.models import CatalogEntry, ShortMeta

DEFAULT_WHISPER_SIZE = "small"  # multilingual (FR/AR/EN)
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
FIXED_TAGS = ["moto", "motovlog", "shorts"]
MAX_TITLE = 100
MAX_TAGS = 15
MAX_WORDS_PER_CUE = 4


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class AssStyle:
    font: str = "Arial"
    size: int = 72
    primary: str = "&H00FFFFFF"  # white
    outline_color: str = "&H00000000"
    back: str = "&H80000000"
    outline: int = 5
    shadow: int = 3
    margin_v: int = 380  # lower third on a 1920-tall canvas
    play_w: int = 1080
    play_h: int = 1920


DEFAULT_STYLE = AssStyle()


# ---------------------------------------------------------------- transcribe
def _default_model():
    from faster_whisper import WhisperModel

    return WhisperModel(DEFAULT_WHISPER_SIZE, device="auto", compute_type="auto")


def transcribe(path: str, model=None) -> list[Segment]:
    """Transcribe with faster-whisper (auto language). [] when no speech.

    `model` is any object with `.transcribe(path, ...) -> (segments, info)`.
    """
    model = model if model is not None else _default_model()
    segs, _info = model.transcribe(path, vad_filter=True)
    out = []
    for s in segs:
        text = (s.text or "").strip()
        if text:
            out.append(Segment(float(s.start), float(s.end), text))
    return out


# ----------------------------------------------------------------------- ASS
def _ts(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _split_cues(seg: Segment) -> list[Segment]:
    """Split a segment into short phrases, time shared by word count."""
    words = seg.text.split()
    if not words:
        return []
    chunks = [words[i : i + MAX_WORDS_PER_CUE] for i in range(0, len(words), MAX_WORDS_PER_CUE)]
    dur = max(seg.end - seg.start, 0.0)
    total = len(words)
    cues, t = [], seg.start
    for c in chunks:
        d = dur * len(c) / total
        cues.append(Segment(t, t + d, " ".join(c)))
        t += d
    return cues


def render_ass(segments: list[Segment], style: AssStyle = DEFAULT_STYLE) -> str:
    """Pure: ASS subtitle text. Bold white, heavy outline/shadow, bottom-center."""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {style.play_w}",
        f"PlayResY: {style.play_h}",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{style.font},{style.size},{style.primary},{style.primary},"
        f"{style.outline_color},{style.back},-1,0,0,0,100,100,0,0,1,{style.outline},"
        f"{style.shadow},2,60,60,{style.margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for seg in segments:
        for cue in _split_cues(seg):
            text = cue.text.replace("\n", " ").replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{_ts(cue.start)},{_ts(cue.end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def build_ass(
    segments: list[Segment], style: AssStyle = DEFAULT_STYLE, out_path: str | None = None
) -> str:
    """Write the ASS file and return its path."""
    content = render_ass(segments, style)
    if out_path is None:
        fd, out_path = tempfile.mkstemp(suffix=".ass")
        os.close(fd)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


# ------------------------------------------------------------------ metadata
_HANDLE = re.compile(r"(^|\s)@\w[\w.]*")
_SPAM_TAG = re.compile(r"#(fyp|foryou|foryoupage|fy|viral|xyzbca|pourtoi)\w*", re.I)
_PLATFORM = re.compile(r"\b(tik\s?tok|instagram|insta|reels?)\b", re.I)


def _clean_title(title: str) -> str:
    t = _HANDLE.sub(" ", str(title))
    t = _SPAM_TAG.sub(" ", t)
    t = _PLATFORM.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -|:,.")
    return t[:MAX_TITLE].rstrip()


def _norm_tag(tag: str) -> str:
    return re.sub(r"\s+", "", str(tag).lstrip("#")).lower()


def sanitize_meta(raw: dict) -> ShortMeta:
    """Pure/deterministic: enforce YouTube limits and strip cross-platform noise."""
    title = _clean_title(raw.get("title", ""))
    description = str(raw.get("description", "")).strip()
    seen: list[str] = []
    for t in raw.get("tags", []):
        n = _norm_tag(t)
        if n and n not in seen and not _SPAM_TAG.fullmatch("#" + n) and not _PLATFORM.search(n):
            seen.append(n)
    kept = seen[: MAX_TAGS - len(FIXED_TAGS)]
    tags = kept + [t for t in FIXED_TAGS if t not in kept]
    return ShortMeta(title=title, description=description, tags=tags[:MAX_TAGS])


def _build_prompt(entry: CatalogEntry, transcript_text: str) -> str:
    return (
        "You write YouTube Shorts metadata for a motorcycle channel (night riding). "
        "Rewrite in your own words, YouTube-native, never copying the original caption. "
        "Do not mention TikTok, Instagram, other platforms or @handles. "
        'Reply ONLY with JSON: {"title": str (<=100 chars), "description": str, '
        '"tags": [str, ...up to 12]}.\n\n'
        f"Original caption: {entry.caption}\n"
        f"Spoken transcript: {transcript_text}\n"
    )


def gemini_call(prompt: str, model_name: str | None = None) -> str:
    """Default LLM: Gemini via GEMINI_API_KEY (free key). Public content only.

    Model name: `model_name` arg (callers pass Settings.gemini_model), else
    env GEMINI_MODEL, else a default.
    """
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    name = model_name or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    model = genai.GenerativeModel(name)
    resp = model.generate_content(
        prompt, generation_config={"response_mime_type": "application/json"}
    )
    return resp.text


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")
    return data


def generate_metadata(
    entry: CatalogEntry,
    transcript_text: str,
    llm: Callable[[str], str] = gemini_call,
) -> ShortMeta:
    """Prompt the LLM with caption + transcript, parse JSON, sanitize."""
    return sanitize_meta(_parse_json(llm(_build_prompt(entry, transcript_text))))
