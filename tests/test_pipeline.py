from social2shorts.enrich import Segment
from social2shorts.config import Settings
from social2shorts.ingest import FakePhoneSource
from social2shorts.models import CatalogEntry, Original, Platform, ShortMeta
from social2shorts.pipeline import PipelineDeps, run_backlog
from social2shorts.youtube_upload import AuthRefreshError


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


def _multi(n_match):
    """Entries A1..An all match an original each; A1 has most views (processed first)."""
    ents, origs = [], []
    for k in range(n_match):
        sid = f"M{k}"
        ents.append(_e(sid, 20.0 + 3 * k).model_copy(update={"view_count": 100000 - k}))
        origs.append(Original(path=f"/orig/{sid}.mp4", duration=20.0 + 3 * k))
    return ents, origs


def _deps_multi(ents, origs, upload):
    d = _deps(upload=upload)
    d.discover = lambda: list(ents)
    d.ingest = lambda s, f, dd: list(origs)
    # distinct deterministic hashes per id; refs and originals share the id
    d.phash_file = lambda p, n: [{"M0": "00000000", "M1": "ffffffff", "M2": "ffff0000"}.get(
        p.rsplit("/", 1)[-1].split(".")[0], "0f0f0f0f")]
    return d


def test_limit_caps_uploads(tmp_path):
    ents, origs = _multi(3)
    calls = []
    d = _deps_multi(ents, origs, lambda v, m, p: calls.append(v) or "id")
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}), d, limit=1)
    assert len(r.uploaded) == 1 and len(calls) == 1
    assert r.pending == [] and r.review == [] and r.skipped == []


def test_auth_refresh_error_stops_run(tmp_path):
    ents, origs = _multi(3)

    def boom(v, m, p):
        raise AuthRefreshError("x")

    d = _deps_multi(ents, origs, boom)
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}), d, limit=10)
    assert r.alert and not r.uploaded
    assert r.review == [] and r.pending == [] and r.skipped == []


def test_empty_title_replaced(tmp_path):
    seen = []
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}),
                    _deps(title="  ", upload=lambda v, m, p: seen.append(m.title) or "id"), limit=10)
    assert seen == ["cap A"] and len(r.uploaded) == 1


def test_item_error_isolated_and_loop_continues(tmp_path):
    # order by views: A (1000 views) ... all equal views -> stable order A, B, C
    d = _deps()

    def flaky(p):
        raise ZeroDivisionError("boom")

    d.transcribe = flaky
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}), d, limit=10)
    assert not r.uploaded and r.alert is None
    assert "A" in r.review and "B" in r.review      # A failed, B licensed
    assert r.pending == ["C"]                       # later entry still processed
    from social2shorts.db import Store
    reasons = [x["reason"] for x in Store(str(tmp_path / "db.sqlite")).reviews()]
    assert any(x.startswith("processing error: ZeroDivisionError: boom") for x in reasons)


def test_db_error_on_one_entry_does_not_abort(tmp_path, monkeypatch):
    from social2shorts.db import Store
    orig = Store.is_uploaded
    monkeypatch.setattr(Store, "is_uploaded",
                        lambda self, fp: (_ for _ in ()).throw(RuntimeError("db")) if self._n(fp) else orig(self, fp))
    calls = {"n": 0}

    def _n(self, fp):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(Store, "_n", _n, raising=False)
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}), _deps(), limit=10)
    assert len(r.review) >= 1 and r.alert is None


def test_ass_file_removed_after_transcode(tmp_path):
    import dataclasses
    import os
    seen = []

    def tc(src, dst, ass):
        seen.append((ass, ass is not None and os.path.exists(ass)))
        return dst

    deps = dataclasses.replace(_deps(), transcribe=lambda p: [Segment(0.0, 1.0, "hi")], transcode=tc)
    r = run_backlog(_settings(tmp_path), FakePhoneSource({}), deps, limit=10)
    assert len(r.uploaded) == 1
    ass, existed = seen[0]
    assert ass and existed and not os.path.exists(ass)
