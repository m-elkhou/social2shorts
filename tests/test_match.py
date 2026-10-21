from social2shorts.match import match_one
from social2shorts.models import CatalogEntry, ItemStatus, Original, Platform


def _entry(duration: float) -> CatalogEntry:
    return CatalogEntry(platform=Platform.tiktok, source_id="s1", url="u", caption="",
                        duration=duration, view_count=0, like_count=0, comment_count=0,
                        upload_date="20260101", track="original sound", artist="")


def test_match_ambiguous_goes_to_review():
    o1 = Original(path="A.mp4", duration=20.0, phashes=["ffff0000"])
    o2 = Original(path="B.mp4", duration=20.3, phashes=["ffff0001"])
    m = match_one(_entry(20.0), [o1, o2], ref_phashes=["ffff0000"], dur_tol=1.0, phash_threshold=10)
    assert m.status == "ambiguous" and m.original_path is None


def test_match_unique_winner():
    o1 = Original(path="A.mp4", duration=20.0, phashes=["ffff0000"])
    o2 = Original(path="B.mp4", duration=45.0, phashes=["00000000"])
    m = match_one(_entry(20.0), [o1, o2], ref_phashes=["ffff0000"], dur_tol=1.0, phash_threshold=5)
    assert m.status == "matched" and m.original_path == "A.mp4" and m.confidence > 0


def test_match_pending_when_no_duration_candidate():
    o = Original(path="B.mp4", duration=45.0, phashes=["ffff0000"])
    m = match_one(_entry(20.0), [o], ref_phashes=["ffff0000"], dur_tol=1.0, phash_threshold=5)
    assert m.status == ItemStatus.pending_original and m.original_path is None
    assert m.confidence == 0.0
