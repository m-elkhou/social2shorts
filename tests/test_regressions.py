"""One regression test (or more) per finding of the independent code review."""

import os
import shutil
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import NOW, FakeLLM, entry, settled
from googleapiclient.errors import ResumableUploadError

from social2shorts import media, operations
from social2shorts.ai.metadata import CTAS, generate_metadata, sanitize_meta
from social2shorts.db import _V1_LEGACY, Store
from social2shorts.lock import AlreadyRunning, single_instance
from social2shorts.models import Claim, ItemStatus, Platform, Privacy, ReviewKind, Upload
from social2shorts.montage.types import MontageMode
from social2shorts.phash import min_distance, phash_file
from social2shorts.pipeline import LOCK_NAME, RunOptions, SourceChoice, run_pipeline
from social2shorts.publish.contentid import claims_from_item
from social2shorts.publish.quota import QuotaBudget
from social2shorts.publish.youtube import run_resumable
from social2shorts.sources.inbox import scan_inbox

CATALOG = RunOptions(source=SourceChoice.catalog, limit=10)
RAW = RunOptions(source=SourceChoice.raw, limit=None)
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _store(settings) -> Store:
    return Store(settings.db_path)


def _inbox(settings, name: str, content: bytes) -> Path:
    inbox = Path(settings.sources.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_bytes(content)
    return settled(inbox / name)


def _original(settings, deps, stem: str, duration: float, phash: str, content: bytes) -> Path:
    path = Path(settings.originals_dir) / f"{stem}.mp4"
    path.write_bytes(content)
    deps.originals.paths.append(path)
    deps.media.durations[stem] = duration
    deps.media.hashes[stem] = phash
    return path


# 1. Dedupe by source content, not by caption ---------------------------------------
def test_cross_platform_copy_with_other_caption_uploads_once(settings, deps):
    deps.media.durations["A"] = 15.4
    deps.media.hashes["A_ig"] = "ffff0000"  # the IG reference looks like original A too
    deps.catalog.entries = [
        entry("A", 15.4, caption="Night ride #moto"),
        entry("A_ig", 15.5, caption="Night ride #moto #reels", platform=Platform.instagram),
    ]
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.item_key for u in r.uploaded] == ["A"] and r.skipped == ["A_ig"]
    assert _store(settings).item("A_ig").status is ItemStatus.duplicate


def test_emoji_only_captions_do_not_collide(settings, deps):
    _original(settings, deps, "Z", 20.2, "00ff00ff", b"original Z")
    deps.catalog.entries = [entry("A", 20.0, caption="🔥🔥"), entry("Z", 20.2, caption="🏍️")]
    r = run_pipeline(settings, deps, CATALOG)
    assert sorted(u.item_key for u in r.uploaded) == ["A", "Z"]


def test_published_clip_dropped_in_inbox_is_not_reuploaded(settings, deps):
    run_pipeline(settings, deps, CATALOG)
    _inbox(settings, "same.mp4", (Path(settings.originals_dir) / "A.mp4").read_bytes())
    r = run_pipeline(settings, deps, RAW)
    assert r.uploaded == [] and len(r.skipped) == 1 and len(deps.platform.uploads) == 1


# 2. Concurrency and post-upload failures ---------------------------------------------
def test_second_run_is_refused_while_locked(settings, deps):
    with single_instance(Path(settings.work_dir) / LOCK_NAME):
        r = run_pipeline(settings, deps, CATALOG)
    assert r.uploaded == [] and "another run" in r.stop_reason
    assert len(run_pipeline(settings, deps, CATALOG).uploaded) == 1  # lock released


def test_stale_lock_is_replaced(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("123")
    old = time.time() - 7 * 3600
    os.utime(lock, (old, old))
    with single_instance(lock):
        assert lock.read_text() == str(os.getpid())
    assert not lock.exists()
    lock.write_text("123")
    with pytest.raises(AlreadyRunning), single_instance(lock):
        pass


def test_status_is_uploading_during_the_api_call(settings, deps):
    seen = []
    original_upload = deps.platform.upload

    def spy(path, meta, **kw):
        seen.append(_store(settings).item("A").status)
        return original_upload(path, meta, **kw)

    deps.platform.upload = spy
    run_pipeline(settings, deps, CATALOG)
    assert seen == [ItemStatus.uploading]


def test_failed_ledger_write_alerts_and_never_retries(settings, deps, monkeypatch, caplog):
    def broken(self, upload):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Store, "record_upload", broken)
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.youtube_id for u in r.uploaded] == ["yt1"] and "WAS UPLOADED" in caplog.text
    store = _store(settings)
    assert "yt1" in store.open_alerts()[0]["message"]
    assert store.item("A").status is ItemStatus.uploading
    monkeypatch.undo()
    r2 = run_pipeline(settings, deps, CATALOG)
    assert len(deps.platform.uploads) == 1 and "A" in r2.review
    assert store.review("A")["kind"] == ReviewKind.interrupted


# 3. Locked render file on Windows -----------------------------------------------------
def test_cleanup_error_after_upload_is_harmless(settings, deps, monkeypatch):
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if "renders" in str(self):
            raise PermissionError("[WinError 32] used by another process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    deps.platform.claims = [Claim(video_id="yt1", reason="blocked in 2 countries")]
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.catalog, limit=1))
    item = _store(settings).item("A")
    assert len(r.uploaded) == 1 and r.failed == [] and r.claimed == ["A"]  # guard still ran
    assert item.status is ItemStatus.claimed and item.attempts == 0


# 4. Rotation-aware pHash --------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_portrait_original_stored_rotated_matches_upright_copy(tmp_path):
    def ff(*args):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)

    up, raw, rotated = tmp_path / "up.mp4", tmp_path / "raw.mp4", tmp_path / "rot.mp4"
    ff("-f", "lavfi", "-i", "testsrc=s=180x320:d=1:r=10", "-pix_fmt", "yuv420p", str(up))
    ff("-i", str(up), "-vf", "transpose=1", "-pix_fmt", "yuv420p", str(raw))
    ff("-display_rotation", "90", "-i", str(raw), "-c", "copy", str(rotated))
    assert min_distance(phash_file(rotated, 4), phash_file(up, 4)) <= 2


# 5. One bad original ------------------------------------------------------------------
def test_bad_original_is_skipped_and_phone_synced_once(settings, deps):
    _original(settings, deps, "partial", 20.0, "ffff0000", b"")
    real_duration, syncs = deps.media.duration, []

    def duration(path):
        if path.stem == "partial":
            raise ValueError("ffprobe: no duration")
        return real_duration(path)

    def sync(dest):
        syncs.append(dest)
        return list(deps.originals.paths)

    deps.media.duration = duration
    deps.originals.sync = sync
    deps.catalog.entries = [entry(k, 20.0 + 3 * i) for i, k in enumerate("AXYZ")]
    r = run_pipeline(settings, deps, CATALOG)
    assert len(syncs) == 1 and [u.item_key for u in r.uploaded] == ["A"] and r.failed == []


# 6. System-wide faults ----------------------------------------------------------------
def test_systemic_failure_stops_run_without_penalizing_items(settings, deps):
    for i in range(6):
        _inbox(settings, f"c{i}.mp4", f"x{i}".encode())
    calls = []

    def boom(path):
        calls.append(path)
        raise RuntimeError("whisper model download failed")

    deps.transcriber.transcribe = boom
    for _ in range(3):
        r = run_pipeline(settings, deps, RAW)
    store = _store(settings)
    assert len(calls) == 9 and "system problem" in r.stop_reason and store.open_alerts()
    assert store.reviews() == [] and all(i.attempts == 0 for i in store.items())


def test_different_errors_still_count_per_item(settings, deps):
    for i in range(3):
        _inbox(settings, f"c{i}.mp4", f"x{i}".encode())
    deps.transcriber.transcribe = lambda path: (_ for _ in ()).throw(OSError(f"bad {path.name}"))
    r = run_pipeline(settings, deps, RAW)
    assert len(r.failed) == 3 and r.stop_reason is None
    assert all(i.attempts == 1 for i in _store(settings).items())


# 7. Raw clip length -------------------------------------------------------------------
def test_short_raw_clip_goes_to_review_and_can_be_approved(settings, deps):
    path = _inbox(settings, "tiny.mp4", b"tiny")
    deps.media.durations["tiny"] = 5.0
    r = run_pipeline(settings, deps, RAW)
    key = r.review[0]
    store = _store(settings)
    reason = store.review(key)["reason"]
    assert store.item(key).status is ItemStatus.too_short
    assert "too short" in reason and path.name in reason
    operations.approve(store, key)
    assert [u.item_key for u in run_pipeline(settings, deps, RAW).uploaded] == [key]


def test_raw_mode_depends_on_clip_length(settings, deps):
    _inbox(settings, "short.mp4", b"1")
    _inbox(settings, "long.mp4", b"2")
    deps.media.durations.update({"short": 20.0, "long": 300.0})
    run_pipeline(settings, deps, RAW)
    modes = {c["src"].stem: c["mode"] for c in deps.renderer.calls}
    assert modes == {"short": MontageMode.polish, "long": MontageMode.highlight}


# 8. publish all is conservative -------------------------------------------------------
def test_publish_all_skips_claimed_and_scheduled(settings, deps):
    _original(settings, deps, "Z", 25.0, "00ff00ff", b"original Z")
    deps.catalog.entries = [entry("A", 20.0), entry("Z", 25.0)]
    deps.platform.claims = [Claim(video_id="yt1", reason="blocked in 200 countries")]
    run_pipeline(settings, deps, CATALOG)
    store = _store(settings)
    store.record_upload(Upload(youtube_id="ytS", item_key="S", fingerprint="s",
                               content_sha256="s", privacy="private",
                               publish_at=NOW + timedelta(days=1), uploaded_at=NOW))  # fmt: skip
    budget = QuotaBudget(store, 10_000, deps.clock)
    deps.platform.privacy_changes.clear()
    result = operations.publish(store, deps.platform, budget, "all", "public")
    assert deps.platform.privacy_changes == [("yt2", "public")]
    assert {k for k, _ in result.excluded} == {"A", "S"}
    assert store.item("A").status is ItemStatus.claimed and store.review("A")
    operations.publish(store, deps.platform, budget, "A", "public", include_claimed=True)
    assert store.item("A").status is ItemStatus.uploaded and store.review("A") is None


# 9. Windows console encoding ----------------------------------------------------------
def test_ffprobe_decodes_utf8_with_replacement(monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(args, 0, stdout="12.5\n")

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert media.ffprobe_duration(Path("vidéo é.mp4")) == 12.5
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"


# 10. Metadata YouTube rejects, and no retry on client errors --------------------------
def test_angle_brackets_removed_from_metadata(settings, deps):
    deps.llm = FakeLLM(reply='{"title": "Night ride <3 with the boys >>", '
                             '"description": "a <b> c", "tags": ["x"], "hook": "h<"}')  # fmt: skip
    run_pipeline(settings, deps, CATALOG)
    meta = deps.platform.uploads[0]["meta"]
    assert not any(ch in meta.title + meta.description + meta.hook for ch in "<>")


class _Chunks:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def next_chunk(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return None, outcome


def _resumable_error(status: int) -> ResumableUploadError:
    from types import SimpleNamespace

    return ResumableUploadError(SimpleNamespace(status=status, reason=""), b"{}")


def test_client_errors_not_retried_but_429_is():
    with pytest.raises(ResumableUploadError):
        run_resumable(_Chunks(_resumable_error(400)), sleep=pytest.fail)
    sleeps = []
    assert run_resumable(_Chunks(_resumable_error(429), {"id": "v"}), sleep=sleeps.append)
    assert len(sleeps) == 1


def test_failed_insert_is_charged_once(settings, deps):
    deps.platform.upload_error = RuntimeError("network gone after retries")
    run_pipeline(settings, deps, RunOptions(source=SourceChoice.catalog, limit=1))
    units, uploads = _store(settings).quota_used("2026-10-23")
    assert uploads == 1 and units == settings.youtube.upload_cost_units


# 11. Inbox files still being copied or locked -----------------------------------------
def test_inbox_skips_fresh_and_unreadable_files(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "copying.mp4").write_bytes(b"1")  # just written: still being copied
    for name in ("locked.mp4", "ok.mp4"):
        (inbox / name).write_bytes(name.encode())
        settled(inbox / name)
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    real = media.content_hash

    def maybe_locked(path, store):
        if path.name == "locked.mp4":
            raise PermissionError("[WinError 32]")
        return real(path, store)

    monkeypatch.setattr("social2shorts.sources.inbox.content_hash", maybe_locked)
    found = scan_inbox(inbox, store, now=NOW.timestamp())
    assert [v.path.name for v in found] == ["ok.mp4"]


# 12. Legacy database ------------------------------------------------------------------
def test_migration_normalizes_timestamps_and_claims(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(_V1_LEGACY)
        c.execute(
            "INSERT INTO catalog VALUES ('t1','tiktok','u','Hello',12,1,1,1,'20260101',"
            "'original sound','','{}','uploaded')"
        )
        c.execute(
            "INSERT INTO ledger (fingerprint, source_id, youtube_id, created) "
            "VALUES ('fp1', 't1', 'YT1', '2026-10-20 08:00:00')"
        )
        c.execute(
            "INSERT INTO review_queue (source_id, reason) VALUES ('t1', 'Content-ID claim: x')"
        )
    store = Store(path)
    store.init_schema()
    upload = store.uploads()[0]
    assert upload.uploaded_at == datetime(2026, 10, 20, 8, tzinfo=UTC)
    assert store.uploads(since=datetime(2026, 10, 19, tzinfo=UTC)) == [upload]
    assert store.review("t1")["kind"] == "claim" and store.item("t1").status is ItemStatus.claimed


# 13. Fallback variety and language ----------------------------------------------------
def test_fallback_titles_are_varied_and_french_by_default():
    hints = [f"VID_2026{i % 12 + 1:02d}{i % 28 + 1:02d}_{i % 24:02d}0000" for i in range(200)]
    metas = [
        generate_metadata(None, "", "", key=f"raw-{i:04d}", hint=h) for i, h in enumerate(hints)
    ]
    assert len({m.title for m in metas}) >= 120
    fr_ctas = {c for c in CTAS["fr"] if c}
    assert any(c in m.description for m in metas for c in fr_ctas)
    again = generate_metadata(None, "", "", key="raw-0007", hint="VID_20260808_070000")
    assert again.title == generate_metadata(None, "", "", key="raw-0007",
                                            hint="VID_20260808_070000").title  # fmt: skip


def test_transcript_keywords_feed_fallback():
    m = generate_metadata(None, "", "la corniche était vide, toute la corniche", key="raw-1")
    assert "corniche" in m.title and "corniche" in m.tags


def test_cta_language_follows_config():
    ar = {c for c in CTAS["ar"] if c}
    descriptions = [
        sanitize_meta(
            {"title": "t", "description": "d"}, caption="", key=f"k{i}", language="ar"
        ).description
        for i in range(30)
    ]
    assert any(c in d for d in descriptions for c in ar)


# 14. Region allow-lists ---------------------------------------------------------------
def test_region_allow_list_is_a_restriction():
    item = {"id": "v", "contentDetails": {"regionRestriction": {"allowed": ["MA"]}}}
    assert [c.reason for c in claims_from_item(item)] == ["only viewable in 1 countries"]


def test_scheduled_privacy_still_works_with_lock(settings, deps):
    settings.schedule.timezone = "Europe/Paris"
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.catalog, limit=1,
                                                privacy=Privacy.scheduled))  # fmt: skip
    assert r.uploaded[0].publish_at == datetime(2026, 10, 23, 10, tzinfo=UTC)
