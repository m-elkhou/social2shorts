from insta2youtube.config import Settings
from insta2youtube.ingest import FakePhoneSource
from insta2youtube.models import CatalogEntry, Original, Platform, ShortMeta
from insta2youtube.pipeline import PipelineDeps, run_backlog
from insta2youtube.youtube_upload import AuthRefreshError


def _e(sid, dur, track="original sound"):
    return CatalogEntry(platform=Platform.tiktok, source_id=sid, url=f"http://x/{sid}",
                        caption=f"cap {sid}", duration=dur, view_count=1000,
                        like_count=10, comment_count=1, upload_date="20260101",
                        track=track, artist="")


ENTRIES = [_e("A", 20.0), _e("B", 30.0, track="Some Song"), _e("C", 40.0)]


def _settings(tmp_path):
    return Settings(tiktok_profile_url="u", youtube_handle="h",
                    originals_dir=str(tmp_path / "o"), work_dir=str(tmp_path / "w"),
                    db_path=str(tmp_path / "db.sqlite"), phone_video_folders=["F"])


def _deps(upload=None, title="T"):
    return PipelineDeps(
        discover=lambda: list(ENTRIES),
        download_reference=lambda url, wd: f"/ref/{url.rsplit('/', 1)[-1]}.mp4",
        ingest=lambda s, f, d: [Original(path="/orig/A.mp4", duration=20.0)],
        phash_file=lambda p, n: ["ffff0000"] if p in ("/ref/A.mp4", "/orig/A.mp4") else ["00000000"],
        transcribe=lambda p: [],
        generate_meta=lambda e, t: ShortMeta(title=title, description="d", tags=[]),
        upload=upload or (lambda v, m, p: "yt123"),
        transcode=lambda s, d, a: d,
    )


def test_backlog_routes_and_is_idempotent(tmp_path):
    s = _settings(tmp_path)
    phone = FakePhoneSource({})
    r = run_backlog(s, phone, _deps(), limit=10)
    assert len(r.uploaded) == 1 and len(r.review) == 1 and len(r.pending) == 1
    r2 = run_backlog(s, phone, _deps(), limit=10)
    assert len(r2.uploaded) == 0 and r2.skipped == ["A"]


def test_auth_refresh_error_stops_run(tmp_path):
    def boom(v, m, p):
        raise AuthRefreshError("x")

    r = run_backlog(_settings(tmp_path), FakePhoneSource({}), _deps(upload=boom), limit=10)
    assert r.alert and not r.uploaded


def test_empty_title_replaced_and_item_errors_isolated(tmp_path):
    seen = []
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}),
                    _deps(title="  ", upload=lambda v, m, p: seen.append(m.title) or "id"), limit=10)
    assert seen == ["cap A"] and len(r.uploaded) == 1

    d = _deps()
    d.transcribe = lambda p: 1 / 0
    sub = tmp_path / "second"
    sub.mkdir()
    r = run_backlog(_settings(sub), FakePhoneSource({}), d, limit=10)
    assert not r.uploaded and len(r.review) == 2
