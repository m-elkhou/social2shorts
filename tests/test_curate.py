from insta2youtube.config import Settings
from insta2youtube.curate import curate, is_eligible, score
from insta2youtube.models import CatalogEntry, Platform


def _entry(duration=30.0, view_count=100, like_count=0, comment_count=0, sid="x"):
    return CatalogEntry(
        platform=Platform.tiktok, source_id=sid, url="u", caption="", duration=duration,
        view_count=view_count, like_count=like_count, comment_count=comment_count,
        upload_date="20260101", track="", artist="",
    )


def _settings(**kw):
    return Settings(
        tiktok_profile_url="u", youtube_handle="h", originals_dir="o", work_dir="w", db_path="d", **kw
    )


def test_excludes_microclips_and_slideshows():
    short = _entry(duration=6)
    ok = _entry(duration=30)
    slideshow = _entry(duration=0.0)
    assert is_eligible(short, 12.0) is False
    assert is_eligible(ok, 12.0) is True
    assert is_eligible(slideshow, 12.0) is False


def test_no_duration_flag_off_still_respects_min():
    assert is_eligible(_entry(duration=0.0), 0.0, exclude_no_duration=False) is True
    assert is_eligible(_entry(duration=0.0), 0.0, exclude_no_duration=True) is False


def test_curate_sorts_by_engagement():
    a = _entry(duration=30, view_count=100, sid="a")
    b = _entry(duration=30, view_count=1000, sid="b")
    out = curate([a, b], _settings(min_duration_s=12))
    assert out[0].view_count == 1000


def test_curate_drops_ineligible():
    out = curate([_entry(duration=5), _entry(duration=0.0), _entry(duration=30)], _settings())
    assert len(out) == 1


def test_score_views_dominate_and_engagement_helps():
    assert score(_entry(view_count=10000)) > score(_entry(view_count=100))
    assert score(_entry(view_count=1000, like_count=300)) > score(_entry(view_count=1000, like_count=0))


def test_score_sweet_spot_bonus():
    assert score(_entry(duration=30)) > score(_entry(duration=120))
    assert score(_entry(duration=30)) > score(_entry(duration=13))
