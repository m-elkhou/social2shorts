from social2shorts.db import Store
from social2shorts.models import CatalogEntry, Platform

def test_cross_platform_dedupe(tmp_path):
    s = Store(str(tmp_path/"db.sqlite"))
    s.init_schema()

    # Two entries: one TikTok, one Instagram.
    # Same caption ("road") and duration (20.0) -> should share fingerprint.
    e_tt = CatalogEntry(
        platform=Platform.tiktok,
        source_id="tt123",
        url="u1",
        caption="Road trip!",
        duration=20.1,
        view_count=5, like_count=1, comment_count=0,
        upload_date="20260101", track="original sound", artist="x", raw={}
    )
    e_ig = CatalogEntry(
        platform=Platform.instagram,
        source_id="ig456",
        url="u2",
        caption="ROAD TRIP!",
        duration=20.4,
        view_count=10, like_count=2, comment_count=0,
        upload_date="20260102", track="original sound", artist="y", raw={}
    )

    fp_tt = s.fingerprint(e_tt)
    fp_ig = s.fingerprint(e_ig)

    assert fp_tt == fp_ig, f"Fingerprints should match: {fp_tt} vs {fp_ig}"

    # Mark TikTok version as uploaded
    s.mark_uploaded(fp_tt, e_tt.source_id, "yt_video_1")

    # Check if Instagram version is now considered "uploaded" via the shared fingerprint
    assert s.is_uploaded(fp_ig) is True, "Instagram copy should be marked as uploaded because TikTok copy is"
