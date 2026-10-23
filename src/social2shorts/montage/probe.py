"""Read technical facts about a clip (size, fps, duration, audio, rotation) via ffprobe."""

from __future__ import annotations

import json
import subprocess

from .errors import MontageError
from .types import MediaInfo


def _fraction(value: str | None) -> float:
    """Parse an ffprobe rate such as ``"30000/1001"``; 0.0 when unknown."""
    if not value or value in ("0/0", "N/A"):
        return 0.0
    num, _, den = value.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    """Clockwise display rotation in degrees (0/90/180/270) from side data or legacy tags."""
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return round(-float(side["rotation"])) % 360
    rotate = stream.get("tags", {}).get("rotate")
    return int(rotate) % 360 if rotate else 0


def parse_probe(data: dict, path: str) -> MediaInfo:
    """Turn ffprobe JSON into :class:`MediaInfo`.

    Width/height are the *displayed* (post-rotation) dimensions, because ffmpeg
    autorotates frames before they reach the filter graph.

    Raises:
        MontageError: If the file has no video stream.
    """
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MontageError(f"no video stream in {path}")
    rotation = _rotation(video)
    width, height = int(video["width"]), int(video["height"])
    if rotation in (90, 270):
        width, height = height, width
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")) or 30.0
    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        rotation=rotation,
    )


def probe(src: str) -> MediaInfo:
    """Probe ``src`` with ffprobe.

    Raises:
        MontageError: If ffprobe fails or the file has no video.
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", src]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MontageError(f"ffprobe failed on {src}: {proc.stderr.strip()[-500:]}")
    return parse_probe(json.loads(proc.stdout or "{}"), src)
