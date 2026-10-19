import json
from pathlib import Path

import pytest

from insta2youtube.discover import DiscoveryError, discover_tiktok, download_reference, parse_entry

FIX = Path(__file__).parent / "fixtures"


def test_parse_entry_maps_fields():
    info = json.load(open(FIX / "tiktok_entry.json"))
    e = parse_entry(info)
    assert e.platform == "tiktok" and e.source_id == "7667654234271255828" and e.duration == 20 and e.track == "original sound"
    assert e.caption.startswith("Sunday ride") and e.raw["repost_count"] == 5


def test_parse_entry_slideshow_without_duration():
    e = parse_entry({"id": "1", "webpage_url": "u"})
    assert e.duration == 0.0 and e.view_count == 0 and e.track == ""


def test_discover_raises_on_empty():
    with pytest.raises(DiscoveryError):
        discover_tiktok("url", None, ytdlp=lambda *a, **k: [])


def test_discover_maps_entries_and_limit():
    infos = json.load(open(FIX / "tiktok_flat.json"))
    seen = {}

    def fake(url, opts=None, download=False):
        seen["opts"] = opts
        return infos

    out = discover_tiktok("url", 5, ytdlp=fake)
    assert [e.source_id for e in out] == ["7667654234271255828", "7600000000000000002"]
    assert out[1].duration == 0.0
    assert seen["opts"]["playlistend"] == 5


def test_download_reference_returns_path(tmp_path):
    def fake(url, opts=None, download=False):
        assert download and "540p" in opts["format"]
        return [{"id": "9", "requested_downloads": [{"filepath": str(tmp_path / "9.mp4")}]}]

    assert download_reference("u", tmp_path / "refs", ytdlp=fake) == str(tmp_path / "9.mp4")
    assert (tmp_path / "refs").is_dir()


def test_build_opts_defaults_and_ytdlp_constructs():
    import yt_dlp

    from insta2youtube.discover import build_opts

    o = build_opts({"playlistend": 3})
    assert "impersonate" not in o and o["playlistend"] == 3 and o["ignoreerrors"]
    yt_dlp.YoutubeDL(o).close()  # builds without raising, no curl_cffi needed


@pytest.mark.parametrize("empty", [[], None])
def test_download_reference_empty_raises(tmp_path, empty):
    with pytest.raises(DiscoveryError):
        download_reference("u", tmp_path, ytdlp=lambda *a, **k: empty)


def test_download_reference_fallback_path_and_format(tmp_path):
    seen = {}

    def fake(url, opts=None, download=False):
        seen["opts"] = opts
        return [{"id": "42", "ext": "mp4"}]

    got = download_reference("u", tmp_path / "refs", ytdlp=fake)
    assert got == str(tmp_path / "refs" / "42.mp4")
    assert seen["opts"]["format"].split("/")[0] == "h264_540p*"
    assert seen["opts"]["outtmpl"].startswith(str(tmp_path / "refs"))
