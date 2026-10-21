"""
Transcoding module.

This module handles the conversion of original videos to YouTube Shorts
format, including scaling, padding, and burning in subtitles.
"""
from __future__ import annotations

import subprocess


def _escape_filter_path(path: str) -> str:
    """
    Escape a path for use inside an ffmpeg filter option value.

    Args:
        path (str): The path to escape.

    Returns:
        str: The escaped path.
    """
    p = path.replace("\\", "/")
    p = p.replace(":", r"\:").replace("'", r"\'")
    return p


def build_args(
    src: str,
    dst: str,
    target: tuple[int, int] = (1080, 1920),
    subtitle_ass: str | None = None,
) -> list[str]:
    """
    Build the ffmpeg arguments for transcoding a video to a Short.

    Args:
        src (str): Path to the source video.
        dst (str): Path to the destination video.
        target (tuple[int, int]): Target resolution (width, height).
        subtitle_ass (Optional[str]): Path to the ASS subtitle file.

    Returns:
        list[str]: The list of arguments for the ffmpeg command.
    """
    w, h = target
    filters = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    ]
    if subtitle_ass:
        filters.append(f"ass='{_escape_filter_path(subtitle_ass)}'")
    return [
        "ffmpeg", "-y", "-i", src,
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        dst,
    ]


def to_short(src: str, dst: str, subtitle_ass: str | None = None) -> str:
    """
    Transcode a video to YouTube Shorts format.

    Args:
        src (str): Path to the source video.
        dst (str): Path to the destination video.
        subtitle_ass (Optional[str]): Path to the ASS subtitle file.

    Returns:
        str: Path to the transcoded video.
    """
    subprocess.run(build_args(src, dst, subtitle_ass=subtitle_ass), check=True)
    return dst
