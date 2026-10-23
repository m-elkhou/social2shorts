import json
import shutil
import time
from pathlib import Path

import pytest

from social2shorts.db import Store
from social2shorts.models import Platform
from social2shorts.media import sha256_file
from social2shorts.sources.inbox import SETTLE_S, scan_inbox
from social2shorts.sources.phone import (
    FileNotOnPhone,
    PhoneNotFound,
    PhoneOriginals,
    PhoneVideo,
    sync_originals,
)
from social2shorts.sources.tiktok_instagram import (
    DiscoveryError,
    SocialCatalog,
    build_opts,
    discover_profile,
    download_reference,
    parse_entry,
)

FIX = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------- yt-dlp adapter
def test_parse_entry_maps_fields():
    e = parse_entry(json.loads((FIX / "tiktok_entry.json").read_text()))
    assert e.platform is Platform.tiktok and e.source_id == "7667654234271255828"
    assert e.duration == 20 and e.track == "original sound" and e.raw["repost_count"] == 5


def test_parse_entry_slideshow_without_duration():
    e = parse_entry({"id": "1", "webpage_url": "u"})
    assert e.duration == 0.0 and e.view_count == 0 and e.track == ""


def test_discover_profile_options_and_empty():
    infos = json.loads((FIX / "tiktok_flat.json").read_text())
    seen = {}

    def fake(url, opts=None, download=False):
        seen["opts"] = opts
        return infos

    out = discover_profile("url", Platform.instagram, 5, "cookies.txt", ytdlp=fake)
    assert [e.platform for e in out] == [Platform.instagram] * 2
    assert seen["opts"] == {"playlistend": 5, "cookiefile": "cookies.txt"}
    with pytest.raises(DiscoveryError):
        discover_profile("url", Platform.tiktok, ytdlp=lambda *a, **k: [])


def test_social_catalog_skips_failing_platform():
    def fake(url, opts=None, download=False):
        if "instagram" in url:
            raise RuntimeError("login required")
        return [{"id": "1", "duration": 20}]

    cat = SocialCatalog("https://tiktok/x", "https://instagram/x", "c.txt", ytdlp=fake)
    assert [e.source_id for e in cat.discover()] == ["1"]
    assert SocialCatalog(ytdlp=fake).discover() == []  # nothing configured


def test_download_reference_paths(tmp_path):
    def with_path(url, opts=None, download=False):
        assert download and opts["format"].startswith("h264_540p")
        return [{"id": "9", "requested_downloads": [{"filepath": str(tmp_path / "9.mp4")}]}]

    assert download_reference("u", tmp_path / "refs", with_path) == tmp_path / "9.mp4"
    fallback = download_reference("u", tmp_path / "r", lambda *a, **k: [{"id": "42"}])
    assert fallback == tmp_path / "r" / "42.mp4"
    with pytest.raises(DiscoveryError):
        download_reference("u", tmp_path, lambda *a, **k: [])


def test_build_opts_merges():
    o = build_opts({"playlistend": 3})
    assert o["playlistend"] == 3 and o["ignoreerrors"] and o["quiet"]


# --------------------------------------------------------------- phone adapter
class FakeDevice:
    def __init__(self, files: dict[str, Path], folder: str = "Movies/Edits"):
        self.files, self.folder, self.copied = files, folder, []

    def list_videos(self, folders):
        if self.folder not in folders:
            raise FileNotOnPhone(folders[0])
        return [PhoneVideo(n, self.folder, p.stat().st_size) for n, p in self.files.items()]

    def copy(self, video, dest_dir):
        self.copied.append(video.name)
        dest = dest_dir / video.name
        shutil.copyfile(self.files[video.name], dest)
        return dest


def test_sync_copies_only_missing_files(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"aaa")
    (tmp_path / "b.mp4").write_bytes(b"bb")
    device = FakeDevice({"a.mp4": tmp_path / "a.mp4", "b.mp4": tmp_path / "b.mp4"})
    dest = tmp_path / "originals"
    assert len(sync_originals(device, ["Movies/Edits"], dest)) == 2
    assert sync_originals(device, ["Movies/Edits"], dest, limit=1) == []  # all cached
    (dest / "b.mp4").write_bytes(b"b")  # truncated copy -> size differs -> copied again
    sync_originals(device, ["Movies/Edits"], dest)
    assert device.copied == ["a.mp4", "b.mp4", "b.mp4"]


def test_phone_originals_falls_back_to_cache(tmp_path):
    dest = tmp_path / "originals"
    dest.mkdir()
    (dest / "cached.mp4").write_bytes(b"x")

    def unplugged():
        raise PhoneNotFound("S23")

    assert PhoneOriginals(unplugged, ["Movies/Edits"]).sync(dest) == [dest / "cached.mp4"]


# --------------------------------------------------------------- inbox adapter
def test_scan_inbox_dedupes_and_caches_hash(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    (inbox / "day1").mkdir(parents=True)
    (inbox / "a.mp4").write_bytes(b"same")
    (inbox / "day1" / "a-copy.MP4").write_bytes(b"same")
    (inbox / "b.mov").write_bytes(b"other")
    (inbox / "notes.txt").write_text("not a video")
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    later = time.time() + 2 * SETTLE_S
    found = scan_inbox(inbox, store, now=later)
    assert len(found) == 2 and found[0].sha256 == sha256_file(inbox / "a.mp4")
    assert found[0].key == f"raw-{found[0].sha256[:16]}"

    def boom(path):
        raise AssertionError("hash should come from the cache")

    monkeypatch.setattr("social2shorts.media.sha256_file", boom)
    assert len(scan_inbox(inbox, store, now=later)) == 2
    assert scan_inbox(tmp_path / "missing", store) == []
