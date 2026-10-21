from insta2youtube.enrich import Segment
from insta2youtube.config import Settings
from insta2youtube.ingest import FakePhoneSource
from insta2youtube.models import CatalogEntry, Original, Platform, ShortMeta
from insta2youtube.pipeline import PipelineDeps, run_backlog, run_continuous
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

def test_run_continuous_processes_only_unseen(tmp_path):
    s = _settings(tmp_path)
    phone = FakePhoneSource({})

    # 1. First run: process A
    d = _deps()
    # Force discover to return only A
    d.discover = lambda: [ENTRIES[0]]
    r1 = run_backlog(s, phone, d, limit=10)

    # We don't assume A uploads here, but we check if it's in the ledger.
    # If A uploaded, it's in ledger. If it failed, it's not.
    # To be sure for the 'continuous' test, let's just manually put A in the ledger.
    from insta2youtube.db import Store
    store = Store(s.db_path)
    store.init_schema()

    # Correct fingerprint for A
    fp_a = store.fingerprint(ENTRIES[0])
    store.mark_uploaded(fp_a, "A", "yt_a")

    # Now run continuous. A should be filtered out.
    d.discover = lambda: ENTRIES
    r2 = run_continuous(s, phone, d, limit=10)

    # A should not be in any report list because it was filtered by run_continuous.
    assert "A" not in r2.uploaded
    assert "A" not in r2.review
    assert "A" not in r2.pending
    assert "A" not in r2.skipped

    # B and C should still be processed.
    assert "B" in r2.review
    assert "C" in r2.pending
