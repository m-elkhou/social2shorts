from social2shorts.models import CatalogEntry


def test_catalog_entry_is_original_audio():
    e = CatalogEntry(platform="tiktok", source_id="1", url="u", caption="c", duration=20, view_count=1, like_count=0, comment_count=0, upload_date="20260101", track="original sound", artist="Moto Ride Mh", raw={})
    assert e.is_original_audio is True
