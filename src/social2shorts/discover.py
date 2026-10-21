"""
Discovery module.

This module handles the extraction of video metadata from social media platforms
(TikTok, Instagram) using yt-dlp.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .models import CatalogEntry

Ytdlp = Callable[..., list[dict]]


class DiscoveryError(RuntimeError):
    """Raised when video discovery fails."""
    pass


def _int(v: Any) -> int:
    """
    Safely convert a value to an integer.

    Args:
        v (Any): The value to convert.

    Returns:
        int: The converted integer, or 0 if conversion fails.
    """
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_entry(info: dict, platform: str = "tiktok") -> CatalogEntry:
    """
    Map a yt-dlp info dict to a CatalogEntry.

    Args:
        info (dict): The info dictionary returned by yt-dlp.
        platform (str): The social media platform. Defaults to "tiktok".

    Returns:
        CatalogEntry: The mapped catalog entry.
    """
    try:
        duration = float(info.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return CatalogEntry(
        platform=platform,
        source_id=str(info["id"]),
        url=info.get("webpage_url") or info.get("url") or "",
        caption=info.get("description") or info.get("title") or "",
        duration=duration,
        view_count=_int(info.get("view_count")),
        like_count=_int(info.get("like_count")),
        comment_count=_int(info.get("comment_count")),
        upload_date=info.get("upload_date") or "",
        track=info.get("track") or "",
        artist=info.get("artist") or "",
        raw=dict(info),
    )


def build_opts(opts: dict | None = None) -> dict[str, Any]:
    """
    Create default yt-dlp options.

    Args:
        opts (Optional[dict]): Additional options to merge.

    Returns:
        dict[str, Any]: The final yt-dlp options dictionary.
    """
    o: dict[str, Any] = {"quiet": True, "no_warnings": True, "ignoreerrors": True}
    o.update(opts or {})
    return o


def run_ytdlp(url: str, opts: dict | None = None, download: bool = False) -> list[dict]:
    """
    Run yt-dlp as a library to extract video information.

    Args:
        url (str): The URL to extract.
        opts (Optional[dict]): Additional options for yt-dlp.
        download (bool): Whether to download the video. Defaults to False.

    Returns:
        list[dict]: A list of info dictionaries (playlist entries, or a single entry).
    """
    import yt_dlp

    ydl_ctx = yt_dlp.YoutubeDL(build_opts(opts))
    with ydl_ctx as ydl:
        info = ydl.extract_info(url, download=download)
    if not info:
        return []
    if "entries" in info:
        return [e for e in (info["entries"] or []) if e]
    return [info]


def discover_tiktok(profile_url: str, limit: int | None, ytdlp: Ytdlp = run_ytdlp) -> list[CatalogEntry]:
    """
    Discover TikTok videos from a profile URL.

    Args:
        profile_url (str): The URL of the TikTok profile.
        limit (Optional[int]): Maximum number of videos to discover.
        ytdlp (Ytdlp): The function used to run yt-dlp. Defaults to run_ytdlp.

    Returns:
        list[CatalogEntry]: A list of discovered TikTok entries.

    Raises:
        DiscoveryError: If no entries are returned.
    """
    opts: dict[str, Any] = {}
    if limit:
        opts["playlistend"] = limit
    infos = ytdlp(profile_url, opts)
    entries = [parse_entry(i) for i in infos if i and i.get("id")]
    if not entries:
        raise DiscoveryError(f"no entries returned for {profile_url}")
    return entries


def discover_instagram(profile_url: str, cookies: str | None, limit: int | None, ytdlp: Ytdlp = run_ytdlp) -> list[CatalogEntry]:
    """
    Discover Instagram videos from a profile URL.

    Args:
        profile_url (str): The URL of the Instagram profile.
        cookies (Optional[str]): Path to the cookies file.
        limit (Optional[int]): Maximum number of videos to discover.
        ytdlp (Ytdlp): The function used to run yt-dlp. Defaults to run_ytdlp.

    Returns:
        list[CatalogEntry]: A list of discovered Instagram entries.

    Raises:
        DiscoveryError: If no entries are returned.
    """
    opts: dict[str, Any] = {}
    if limit:
        opts["playlistend"] = limit
    if cookies:
        opts["cookiefile"] = cookies
    infos = ytdlp(profile_url, opts)
    entries = [parse_entry(i, platform="instagram") for i in infos if i and i.get("id")]
    if not entries:
        raise DiscoveryError(f"no entries returned for {profile_url}")
    return entries


def download_reference(url: str, dest_dir: str | Path, ytdlp: Ytdlp = run_ytdlp) -> str:
    """
    Download a watermark-free 540p reference clip.

    Args:
        url (str): The URL of the video to download.
        dest_dir (str | Path): The directory to save the file.
        ytdlp (Ytdlp): The function used to run yt-dlp. Defaults to run_ytdlp.

    Returns:
        str: The path to the downloaded file.

    Raises:
        DiscoveryError: If the download fails.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "h264_540p*/bytevc1_540p*/best",
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
    }
    infos = ytdlp(url, opts, download=True)
    if not infos:
        raise DiscoveryError(f"download failed for {url}")
    info = infos[0]
    path = (info.get("requested_downloads") or [{}])[0].get("filepath") or info.get("filepath")
    if not path:
        path = str(dest / f"{info['id']}.{info.get('ext', 'mp4')}")
    return str(path)
