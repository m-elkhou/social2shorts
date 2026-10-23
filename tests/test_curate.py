from fakes import entry

from social2shorts.curate import curate, dedupe, is_eligible, score
from social2shorts.models import Platform


def test_excludes_microclips_and_slideshows():
    assert is_eligible(entry("a", 6), 12.0) is False
    assert is_eligible(entry("a", 30), 12.0) is True
    assert is_eligible(entry("a", 0.0), 12.0) is False
    assert is_eligible(entry("a", 0.0), 0.0, exclude_no_duration=False) is True


def test_curate_sorts_by_engagement_and_drops_ineligible():
    out = curate([entry("a", 30, view_count=100), entry("b", 30, view_count=1000),
                  entry("c", 5)], 12.0)  # fmt: skip
    assert [e.source_id for e in out] == ["b", "a"]


def test_score_views_dominate_and_engagement_helps():
    assert score(entry("a", view_count=10000)) > score(entry("a", view_count=100))
    assert score(entry("a", like_count=300)) > score(entry("a", like_count=0))
    assert score(entry("a", 30)) > score(entry("a", 120))


def test_dedupe_prefers_platform():
    tt = entry("tt", 20.1, caption="Road trip!")
    ig = entry("ig", 20.4, caption="ROAD TRIP!", platform=Platform.instagram, view_count=10**6)
    assert dedupe([ig, tt], Platform.tiktok) == [tt]
    assert dedupe([tt, ig], Platform.instagram) == [ig]
    assert len(dedupe([tt, entry("other", 20, caption="different")], Platform.tiktok)) == 2
