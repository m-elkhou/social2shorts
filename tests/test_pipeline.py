from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import NOW, FakeLLM, GeminiDailyQuota, entry, settled

from social2shorts import operations
from social2shorts.db import Store
from social2shorts.models import Claim, ItemStatus, Privacy
from social2shorts.montage.types import MontageMode
from social2shorts.phash import NoVideoStream
from social2shorts.pipeline import (
    MAX_ATTEMPTS,
    RunOptions,
    SourceChoice,
    run_pipeline,
)
from social2shorts.publish.youtube import AuthRefreshError, QuotaExceededError

CATALOG = RunOptions(source=SourceChoice.catalog, limit=10)


def _store(settings) -> Store:
    return Store(settings.db_path)


def _raw(settings, name: str, content: bytes) -> Path:
    inbox = Path(settings.sources.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_bytes(content)
    return settled(path)


def test_catalog_routes_and_is_idempotent(settings, deps):
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.item_key for u in r.uploaded] == ["A"]
    assert r.review == ["B"] and r.pending == ["C"]
    store = _store(settings)
    assert store.item("B").status is ItemStatus.review_audio
    assert store.review("B")["kind"] == "audio"

    r2 = run_pipeline(settings, deps, CATALOG)
    assert r2.uploaded == [] and len(deps.platform.uploads) == 1
    assert r2.review == [] and r2.pending == ["C"]  # B waits for a human, C keeps waiting


def test_catalog_uses_polish_mode_hook_and_words(settings, deps):
    run_pipeline(settings, deps, CATALOG)
    call = deps.renderer.calls[0]
    assert call["mode"] is MontageMode.polish and call["hook"] == "Wait for the turn"
    assert call["words"][0].text == "salut"
    meta = deps.platform.uploads[0]["meta"]
    assert meta.title == "Late ride by the sea" and meta.hook == "Wait for the turn"


def test_reference_downloaded_once_for_pending_posts(settings, deps):
    run_pipeline(settings, deps, CATALOG)
    run_pipeline(settings, deps, CATALOG)
    assert deps.catalog.downloads.count("http://x/C") == 1
    assert not list((Path(settings.work_dir) / "refs").glob("*"))  # references cleaned up


def test_raw_path_highlight_and_content_dedupe(settings, deps):
    _raw(settings, "a.mp4", b"clip-1")
    _raw(settings, "copy-of-a.mp4", b"clip-1")
    _raw(settings, "b.mov", b"clip-2")
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw, limit=10))
    assert len(r.uploaded) == 2
    assert {c["mode"] for c in deps.renderer.calls} == {MontageMode.highlight}
    assert run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw)).uploaded == []


def test_all_sources_interleave(settings, deps):
    _raw(settings, "a.mp4", b"clip-1")
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.all, limit=2))
    keys = [u.item_key for u in r.uploaded]
    assert keys[0] == "A" and keys[1].startswith("raw-")


def test_dry_run_renders_without_youtube(settings, deps):
    deps.platform.upload_error = AssertionError("must not upload")
    deps.platform.claims_error = AssertionError("must not call YouTube")
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.catalog, dry_run=True))
    assert r.rendered == ["A"] and r.alert is None
    assert _store(settings).item("A").status is ItemStatus.rendered
    assert (Path(settings.work_dir) / "renders" / "A.mp4").exists()
    srt = Path(settings.work_dir) / "subtitles" / "A.srt"
    assert "salut" in srt.read_text(encoding="utf-8")  # saved for review


def test_no_speech_removes_an_earlier_subtitle_file(settings, deps):
    srt = Path(settings.work_dir) / "subtitles" / "A.srt"
    srt.parent.mkdir(parents=True)
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nold lyrics\n", encoding="utf-8")
    deps.transcriber.words = []  # music only
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.catalog, dry_run=True))
    assert r.rendered == ["A"] and not srt.exists()


def test_subtitle_track_added_after_upload(settings, deps):
    settings.whisper_language = "ar"
    settings.youtube.caption_name = "Darija"
    run_pipeline(settings, deps, CATALOG)
    [cap] = deps.platform.captions
    assert (cap["video_id"], cap["language"], cap["name"]) == ("yt1", "ar", "Darija")
    assert cap["srt"].splitlines() == ["1", "00:00:00,000 --> 00:00:01,000", "salut"]
    srt = Path(settings.work_dir) / "subtitles" / "A.srt"
    assert srt.read_text(encoding="utf-8") == cap["srt"]  # kept to edit or re-upload by hand
    assert _store(settings).quota_used("2026-10-23")[0] == 1600 + 400 + 1  # insert, captions, guard


def test_caption_language_setting_wins(settings, deps):
    settings.whisper_language, settings.youtube.caption_language = "ar", "fr"
    run_pipeline(settings, deps, CATALOG)
    assert deps.platform.captions[0]["language"] == "fr"


@pytest.mark.parametrize(("language", "enabled"), [(None, True), ("ar", False)])
def test_no_subtitle_track_without_language_or_when_off(settings, deps, language, enabled):
    settings.whisper_language, settings.youtube.upload_captions = language, enabled
    r = run_pipeline(settings, deps, CATALOG)
    assert len(r.uploaded) == 1 and deps.platform.captions == []
    assert _store(settings).open_alerts() == []


def test_subtitle_failure_keeps_the_upload_and_alerts(settings, deps):
    settings.whisper_language = "ar"
    deps.platform.captions_error = RuntimeError("videoNotFound")
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.youtube_id for u in r.uploaded] == ["yt1"] and r.failed == []
    store = _store(settings)
    assert store.item("A").status is ItemStatus.uploaded
    [alert] = store.open_alerts()
    assert "videoNotFound" in alert["message"] and "A.srt" in alert["message"]


def test_subtitle_quota_error_exhausts_the_day(settings, deps):
    settings.whisper_language = "ar"
    deps.platform.captions_error = QuotaExceededError("quotaExceeded")
    r = run_pipeline(settings, deps, CATALOG)
    assert len(r.uploaded) == 1
    assert _store(settings).quota_used("2026-10-23")[0] == settings.youtube.daily_quota_units


def test_ledger_written_before_guard(settings, deps):
    deps.platform.claims_error = RuntimeError("API down")
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.youtube_id for u in r.uploaded] == ["yt1"]
    assert _store(settings).uploads()[0].youtube_id == "yt1"
    run_pipeline(settings, deps, CATALOG)
    assert len(deps.platform.uploads) == 1  # never re-uploaded


def test_claim_makes_video_private_and_queues_review(settings, deps):
    deps.platform.claims = [Claim(video_id="yt1", reason="blocked in 3 countries")]
    r = run_pipeline(settings, deps, CATALOG)
    assert r.claimed == ["A"] and deps.platform.privacy_changes == [("yt1", "private")]
    store = _store(settings)
    assert store.item("A").status is ItemStatus.claimed
    assert store.review("A")["kind"] == "claim"
    assert store.uploads()[0].privacy == "private"


def test_recent_uploads_rechecked_next_run(settings, deps):
    run_pipeline(settings, deps, CATALOG)
    deps.platform.claims = [Claim(video_id="yt1", reason="claimed as licensed content")]
    r = run_pipeline(settings, deps, CATALOG)
    assert r.claimed == ["A"]


def test_quota_exhaustion_stops_cleanly(settings, deps):
    settings.youtube.daily_quota_units = 2000  # room for one upload (+ guard calls)
    _raw(settings, "a.mp4", b"1")
    _raw(settings, "b.mp4", b"2")
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw, limit=None))
    assert len(r.uploaded) == 1 and "quota" in r.stop_reason
    assert len(deps.renderer.calls) == 1  # did not render what it cannot upload


def test_max_uploads_per_day_cap(settings, deps):
    settings.youtube.max_uploads_per_day = 1
    _raw(settings, "a.mp4", b"1")
    _raw(settings, "b.mp4", b"2")
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw, limit=None))
    assert len(r.uploaded) == 1 and "1/1 uploads" in r.stop_reason


def test_youtube_quota_error_stops_and_exhausts_day(settings, deps):
    deps.platform.upload_error = QuotaExceededError("quotaExceeded")
    r = run_pipeline(settings, deps, CATALOG)
    assert r.uploaded == [] and "API quota" in r.stop_reason
    assert _store(settings).quota_used("2026-10-23")[0] == settings.youtube.daily_quota_units


def test_upload_limit_stop_names_the_channel_limit(settings, deps):
    deps.platform.upload_error = QuotaExceededError("x", frozenset({"uploadLimitExceeded"}))
    r = run_pipeline(settings, deps, CATALOG)
    assert r.uploaded == [] and "upload limit" in r.stop_reason
    assert "API quota is not spent" in r.stop_reason


def test_upload_limit_waits_for_its_window_not_the_quota_day(settings, deps):
    deps.platform.upload_error = QuotaExceededError("x", frozenset({"uploadLimitExceeded"}))
    run_pipeline(settings, deps, CATALOG)
    store = _store(settings)
    assert store.upload_limit_refused_at() == NOW
    assert store.quota_used("2026-10-23")[0] < settings.youtube.daily_quota_units
    s = operations.summary(settings, store, NOW)
    assert s.upload_limit_until == NOW + timedelta(hours=24)

    deps.platform.upload_error = None
    deps.renderer.calls.clear()
    r = run_pipeline(settings, deps, CATALOG)  # still inside the window: no render, no Gemini
    assert r.uploaded == [] and deps.renderer.calls == [] and "upload limit" in r.stop_reason

    deps.clock = lambda: NOW + timedelta(hours=24, minutes=1)
    r = run_pipeline(settings, deps, CATALOG)
    assert len(r.uploaded) == 1


def test_auth_failure_alerts_and_stops(settings, deps):
    deps.platform.claims_error = AuthRefreshError("invalid_grant")
    run_pipeline(settings, deps, CATALOG)  # nothing uploaded yet: sweep is a no-op
    deps.platform.upload_error = AuthRefreshError("invalid_grant")
    r = run_pipeline(settings, deps, CATALOG)
    assert r.alert and "social2shorts auth" in r.alert
    assert _store(settings).open_alerts()


def test_scheduled_privacy_uses_free_slots(settings, deps):
    settings.schedule.timezone = "Europe/Paris"
    _raw(settings, "a.mp4", b"1")
    _raw(settings, "b.mp4", b"2")
    opts = RunOptions(source=SourceChoice.raw, limit=2, privacy=Privacy.scheduled)
    r = run_pipeline(settings, deps, opts)
    # NOW = 09:00 UTC = 11:00 Paris (CEST): slots 12:00 and 18:00 local today
    assert [u.publish_at for u in r.uploaded] == [
        datetime(2026, 10, 23, 10, 0, tzinfo=UTC),
        datetime(2026, 10, 23, 16, 0, tzinfo=UTC),
    ]
    assert {u["privacy"] for u in deps.platform.uploads} == {"private"}


def test_private_lock_stores_actual_privacy(settings, deps, caplog):
    deps.platform.force_privacy = "private"
    r = run_pipeline(settings, deps, CATALOG)
    assert r.uploaded[0].privacy == "private"
    assert _store(settings).uploads()[0].privacy == "private"
    assert "locked to private" in caplog.text


def test_llm_failure_falls_back_and_still_uploads(settings, deps):
    deps.llm = FakeLLM(error=RuntimeError("429"))
    r = run_pipeline(settings, deps, CATALOG)
    assert len(r.uploaded) == 1
    assert deps.platform.uploads[0]["meta"].title == "cap A"


def test_failures_retry_then_go_to_review(settings, deps):
    deps.transcriber.transcribe = lambda path: 1 / 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        r = run_pipeline(settings, deps, CATALOG)
        bucket = r.review if attempt == MAX_ATTEMPTS else r.failed
        assert "A" in bucket
    store = _store(settings)
    assert store.item("A").status is ItemStatus.failed and store.review("A")["kind"] == "error"
    assert "A" not in run_pipeline(settings, deps, CATALOG).failed  # no longer retried


def test_approval_of_licensed_audio_processes_next_run(settings, deps):
    run_pipeline(settings, deps, CATALOG)
    operations.approve(_store(settings), "B")
    deps.media.durations["B"] = 30.0
    deps.media.hashes["B"] = "00ff00ff"
    (Path(settings.originals_dir) / "B.mp4").write_bytes(b"original B")
    deps.originals.paths.append(Path(settings.originals_dir) / "B.mp4")
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.item_key for u in r.uploaded] == ["B"]


def test_ambiguous_match_resolved_by_human(settings, deps):
    orig = Path(settings.originals_dir)
    (orig / "A2.mp4").write_bytes(b"twin")
    deps.originals.paths.append(orig / "A2.mp4")
    deps.media.durations["A2"] = 20.3
    deps.media.hashes["A2"] = "ffff0001"
    r = run_pipeline(settings, deps, CATALOG)
    assert "A" in r.review and r.uploaded == []
    store = _store(settings)
    assert len(store.match_candidates("A")) == 2

    operations.approve(store, "A", orig / "A2.mp4")
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.item_key for u in r.uploaded] == ["A"]
    assert deps.renderer.calls[-1]["src"] == orig / "A2.mp4"


def test_cross_platform_duplicate_skipped(settings, deps):
    deps.catalog.entries = [
        entry("A", 20.0, caption="Road trip!"),
        entry("IG1", 20.4, caption="ROAD TRIP", platform="instagram", view_count=99999),
    ]
    r = run_pipeline(settings, deps, CATALOG)
    assert [u.item_key for u in r.uploaded] == ["A"]  # TikTok copy preferred despite views
    assert "IG1" not in r.skipped + r.pending


def test_limit_caps_uploads(settings, deps):
    _raw(settings, "a.mp4", b"1")
    _raw(settings, "b.mp4", b"2")
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw, limit=1))
    assert len(r.uploaded) == 1


def test_missing_raw_file_counts_as_failure(settings, deps):
    path = _raw(settings, "a.mp4", b"1")
    run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw, dry_run=True))
    path.unlink()
    r = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw))
    item = _store(settings).items()[0]
    assert r.failed == [item.key] and item.status is ItemStatus.failed and item.attempts == 1


def test_spent_gemini_quota_stops_without_penalty(settings, deps):
    deps.llm = FakeLLM(error=GeminiDailyQuota())
    r = run_pipeline(settings, deps, CATALOG)
    assert r.uploaded == [] and "Gemini" in r.stop_reason and r.failed == []
    item = _store(settings).item("A")
    assert item.status is not ItemStatus.failed and item.attempts == 0


def test_photo_slideshow_is_skipped_for_good(settings, deps):
    hashes = deps.media.phashes

    def phashes(path, n_frames):
        if path.stem == "ref-C":
            raise NoVideoStream(f"{path} has no video stream")
        return hashes(path, n_frames)

    deps.media.phashes = phashes
    r = run_pipeline(settings, deps, CATALOG)
    assert r.not_video == ["C"] and "C" not in r.pending and r.failed == []
    assert _store(settings).item("C").status is ItemStatus.not_video
    run_pipeline(settings, deps, CATALOG)
    assert deps.catalog.downloads.count("http://x/C") == 1  # never downloaded again
