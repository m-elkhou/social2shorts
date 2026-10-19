from insta2youtube.audio_gate import classify
from insta2youtube.models import CatalogEntry, Platform


def _entry(track: str, artist: str) -> CatalogEntry:
    return CatalogEntry(
        platform=Platform.tiktok,
        source_id="1",
        url="u",
        caption="c",
        duration=20,
        view_count=1,
        like_count=0,
        comment_count=0,
        upload_date="20260101",
        track=track,
        artist=artist,
        raw={},
    )


def test_classify_audio():
    assert classify(_entry(track="original sound", artist="Moto Ride Mh")) == "original"
    assert classify(_entry(track="Blinding Lights", artist="The Weeknd")) == "licensed"
    assert classify(_entry(track="", artist="")) == "original"
