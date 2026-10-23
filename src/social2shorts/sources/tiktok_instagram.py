"""yt-dlp adapter: discovers TikTok/Instagram posts and downloads 540p references for matching."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from social2shorts.models import CatalogEntry, Platform

log = logging.getLogger(__name__)

Ytdlp = Callable[..., list[dict]]


class DiscoveryError(RuntimeError):
    """A profile could not be listed or a reference could not be downloaded."""


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_entry(info: dict, platform: Platform = Platform.tiktok) -> CatalogEntry:
    """Map a yt-dlp info dict to a ``CatalogEntry`` (missing duration -> 0, e.g. slideshows)."""
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
    """Default quiet, error-tolerant yt-dlp options merged with ``opts``."""
    return {"quiet": True, "no_warnings": True, "ignoreerrors": True, **(opts or {})}


def run_ytdlp(url: str, opts: dict | None = None, download: bool = False) -> list[dict]:
    """Run yt-dlp as a library; return playlist entries (or the single entry)."""
    import yt_dlp

    with yt_dlp.YoutubeDL(build_opts(opts)) as ydl:
        info = ydl.extract_info(url, download=download)
    if not info:
        return []
    if "entries" in info:
        return [e for e in (info["entries"] or []) if e]
    return [info]


def discover_profile(
    url: str,
    platform: Platform,
    limit: int | None = None,
    cookies: str | None = None,
    ytdlp: Ytdlp = run_ytdlp,
) -> list[CatalogEntry]:
    """List the posts of one profile.

    Raises:
        DiscoveryError: If yt-dlp returns no usable entry.
    """
    opts: dict[str, Any] = {}
    if limit:
        opts["playlistend"] = limit
    if cookies:
        opts["cookiefile"] = cookies
    entries = [parse_entry(i, platform) for i in ytdlp(url, opts) if i and i.get("id")]
    if not entries:
        raise DiscoveryError(f"no posts returned for {url}")
    return entries


def download_reference(
    url: str, dest_dir: Path, ytdlp: Ytdlp = run_ytdlp, cookies: str | None = None
) -> Path:
    """Download the watermark-free ~540p version of a post (only used for pHash matching).

    Raises:
        DiscoveryError: If the download fails.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "h264_540p*/bytevc1_540p*/best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
    }
    if cookies:
        opts["cookiefile"] = cookies
    infos = ytdlp(url, opts, download=True)
    if not infos:
        raise DiscoveryError(f"download failed for {url}")
    info = infos[0]
    path = (info.get("requested_downloads") or [{}])[0].get("filepath") or info.get("filepath")
    return Path(path) if path else dest_dir / f"{info['id']}.{info.get('ext', 'mp4')}"


class SocialCatalog:
    """``CatalogSource`` adapter over the configured TikTok and Instagram profiles."""

    def __init__(
        self,
        tiktok_url: str = "",
        instagram_url: str = "",
        instagram_cookies: str | None = None,
        limit: int | None = None,
        ytdlp: Ytdlp = run_ytdlp,
    ):
        self.profiles = [(Platform.tiktok, tiktok_url), (Platform.instagram, instagram_url)]
        self.cookies = instagram_cookies
        self.limit = limit
        self.ytdlp = ytdlp

    def discover(self) -> list[CatalogEntry]:
        """Posts from every configured profile; a failing platform is logged and skipped."""
        entries: list[CatalogEntry] = []
        for platform, url in self.profiles:
            if not url:
                continue
            cookies = self.cookies if platform is Platform.instagram else None
            try:
                entries += discover_profile(url, platform, self.limit, cookies, self.ytdlp)
            except Exception as exc:  # network, login wall, profile renamed...
                log.warning("Could not list %s posts from %s: %s", platform.value, url, exc)
        return entries

    def download_reference(self, url: str, dest_dir: Path) -> Path:
        """See :func:`download_reference`."""
        cookies = self.cookies if "instagram.com" in url else None
        return download_reference(url, dest_dir, self.ytdlp, cookies)
