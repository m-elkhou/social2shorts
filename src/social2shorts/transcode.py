from __future__ import annotations

import subprocess


def _escape_filter_path(path: str) -> str:
    """Escape a path for use inside an ffmpeg filter option value."""
    p = path.replace("\\", "/")
    p = p.replace(":", r"\:").replace("'", r"\'")
    return p


def build_args(
    src: str,
    dst: str,
    target: tuple[int, int] = (1080, 1920),
    subtitle_ass: str | None = None,
) -> list[str]:
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
    subprocess.run(build_args(src, dst, subtitle_ass=subtitle_ass), check=True)
    return dst
