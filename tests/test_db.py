from insta2youtube.db import Store
from insta2youtube.models import CatalogEntry


def test_upsert_and_fingerprint_idempotent(tmp_path):
    s = Store(str(tmp_path/"db.sqlite")); s.init_schema()
    e = CatalogEntry(platform="tiktok", source_id="7", url="u", caption="road", duration=20.0, view_count=5, like_count=1, comment_count=0, upload_date="20260101", track="original sound", artist="x", raw={})
    s.upsert_catalog(e); s.upsert_catalog(e)
    assert len(s.all_catalog()) == 1
    fp = s.fingerprint(e); s.mark_uploaded(fp, "7", "yt123")
    assert s.is_uploaded(fp) is True
