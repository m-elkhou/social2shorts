import sqlite3
from datetime import UTC, datetime

from fakes import entry

from social2shorts.db import _V1_LEGACY, SCHEMA_VERSION, Store, catalog_fingerprint
from social2shorts.models import ItemStatus, ReviewKind, SourceKind, Upload


def _store(tmp_path) -> Store:
    s = Store(tmp_path / "db.sqlite")
    s.init_schema()
    return s


def test_fresh_schema_and_idempotent_init(tmp_path):
    s = _store(tmp_path)
    assert s.schema_version() == SCHEMA_VERSION == 2
    assert s.init_schema() == SCHEMA_VERSION


def test_migrates_legacy_database(tmp_path):
    path = tmp_path / "old.sqlite"
    e = entry("7", caption="road")
    with sqlite3.connect(path) as c:
        c.executescript(_V1_LEGACY)
        c.execute(
            "INSERT INTO catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("7", "tiktok", "u", "road", 20.0, 5, 1, 0, "20260101", "", "", "{}", "review_audio"),
        )
        c.execute("INSERT INTO ledger (fingerprint, source_id, youtube_id) VALUES ('f', '9', 'y9')")
        for reason in ("licensed music", "licensed music"):
            c.execute("INSERT INTO review_queue (source_id, reason) VALUES ('7', ?)", (reason,))
    s = Store(path)
    assert s.init_schema() == SCHEMA_VERSION
    item = s.item("7")
    assert item.status is ItemStatus.review_audio and item.fingerprint == catalog_fingerprint(e)
    assert s.is_uploaded("f") and s.uploads()[0].youtube_id == "y9"
    reviews = s.reviews()
    assert len(reviews) == 1 and reviews[0]["kind"] == "audio"


def test_catalog_upsert_creates_one_item(tmp_path):
    s = _store(tmp_path)
    s.upsert_catalog(entry("7"))
    s.upsert_catalog(entry("7", view_count=5))
    items = s.items(SourceKind.catalog)
    assert len(items) == 1 and items[0].entry.view_count == 5


def test_review_queue_one_row_per_item(tmp_path):
    s = _store(tmp_path)
    s.add_review("A", ReviewKind.error, "first")
    s.add_review("A", ReviewKind.audio, "second")
    assert [(r["kind"], r["reason"]) for r in s.reviews()] == [("audio", "second")]
    s.resolve_review("A")
    assert s.reviews() == []


def test_uploads_ledger_and_quota(tmp_path):
    s = _store(tmp_path)
    at = datetime(2026, 10, 24, 11, tzinfo=UTC)
    s.record_upload(Upload(youtube_id="y", item_key="A", fingerprint="fp", privacy="private",
                           publish_at=at, uploaded_at=at, quota_units=1600))  # fmt: skip
    assert s.is_uploaded("fp") and s.publish_times() == [at]
    s.set_upload_privacy("y", "public")
    assert s.uploads()[0].privacy == "public" and s.publish_times() == []
    s.spend_quota("2026-10-23", 1600, uploads=1)
    s.spend_quota("2026-10-23", 1)
    assert s.quota_used("2026-10-23") == (1601, 1) and s.quota_used("2026-10-24") == (0, 0)


def test_caches(tmp_path):
    s = _store(tmp_path)
    s.cache_original("a.mp4", 10, 20.0, ["ff"])
    assert s.cached_original("a.mp4", 10) == (20.0, ["ff"])
    assert s.cached_original("a.mp4", 11) is None  # file changed
    s.cache_hash("a.mp4", 10, 5, "sha")
    assert s.cached_hash("a.mp4", 10, 5) == "sha" and s.cached_hash("a.mp4", 10, 6) is None
