from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .models import CatalogEntry

Ytdlp = Callable[..., list[dict]]


class DiscoveryError(RuntimeError):
    pass


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_entry(info: dict) -> CatalogEntry:
    """Map a yt-dlp TikTok info dict to a CatalogEntry (pure)."""
    try:
        duration = float(info.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return CatalogEntry(
        platform="tiktok",
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
    """Default yt-dlp options (no impersonation: the profile works without it)."""
    o: dict[str, Any] = {"quiet": True, "no_warnings": True, "ignoreerrors": True}
    o.update(opts or {})
    return o


def run_ytdlp(url: str, opts: dict | None = None, download: bool = False) -> list[dict]:
    """Run yt-dlp as a library; return info dicts (playlist entries, or [info])."""
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
    opts: dict[str, Any] = {}
    if limit:
        opts["playlistend"] = limit
    infos = ytdlp(profile_url, opts)
    entries = [parse_entry(i) for i in infos if i and i.get("id")]
    if not entries:
        raise DiscoveryError(f"no entries returned for {profile_url}")
    return entries


def download_reference(url: str, dest_dir: str | Path, ytdlp: Ytdlp = run_ytdlp) -> str:
    """Download the watermark-free 540p clip (matching reference only)."""
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
