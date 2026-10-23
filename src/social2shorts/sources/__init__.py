"""Source adapters: social catalog (yt-dlp), phone originals (MTP) and the raw-video inbox."""

from pathlib import Path

VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".3gp"})


def video_files(folder: Path) -> list[Path]:
    """Video files under ``folder`` (recursive, sorted); empty if the folder does not exist."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
