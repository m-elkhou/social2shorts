"""SQLite repository (``Store``): catalog, work items, reviews, uploads ledger, quota and caches.

The schema is versioned with ``PRAGMA user_version`` and upgraded by ordered migrations.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from social2shorts.models import (
    CatalogEntry,
    Item,
    ItemStatus,
    MatchResult,
    ReviewKind,
    SourceKind,
    Upload,
)

_V1_LEGACY = """
CREATE TABLE IF NOT EXISTS catalog (
    source_id TEXT PRIMARY KEY, platform TEXT NOT NULL, url TEXT NOT NULL,
    caption TEXT NOT NULL, duration REAL NOT NULL, view_count INTEGER NOT NULL,
    like_count INTEGER NOT NULL, comment_count INTEGER NOT NULL, upload_date TEXT NOT NULL,
    track TEXT NOT NULL, artist TEXT NOT NULL, raw TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'discovered'
);
CREATE TABLE IF NOT EXISTS matches (
    source_id TEXT PRIMARY KEY, original_path TEXT, confidence REAL NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, reason TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ledger (
    fingerprint TEXT PRIMARY KEY, source_id TEXT NOT NULL, youtube_id TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_V2_TABLES = """
CREATE TABLE items (
    key TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'discovered', source_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_items_kind_status ON items(kind, status);
CREATE TABLE uploads (
    youtube_id TEXT PRIMARY KEY, item_key TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
    privacy TEXT NOT NULL, publish_at TEXT, uploaded_at TEXT NOT NULL,
    quota_units INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_uploads_item ON uploads(item_key);
CREATE INDEX idx_uploads_publish_at ON uploads(publish_at);
CREATE TABLE quota_usage (
    day TEXT PRIMARY KEY, units INTEGER NOT NULL, uploads INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE originals (
    path TEXT PRIMARY KEY, size INTEGER NOT NULL, duration REAL NOT NULL, phashes TEXT NOT NULL
);
CREATE TABLE ref_phashes (key TEXT PRIMARY KEY, phashes TEXT NOT NULL);
CREATE TABLE file_hashes (
    path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE matches ADD COLUMN candidates TEXT NOT NULL DEFAULT '[]';
ALTER TABLE review_queue ADD COLUMN kind TEXT NOT NULL DEFAULT 'error';
"""

_V3_UPLOADS = """
CREATE TABLE uploads_v3 (
    youtube_id TEXT PRIMARY KEY, item_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
    content_sha256 TEXT, privacy TEXT NOT NULL, publish_at TEXT, uploaded_at TEXT NOT NULL,
    quota_units INTEGER NOT NULL DEFAULT 0
);
INSERT INTO uploads_v3 (youtube_id, item_key, fingerprint, privacy, publish_at, uploaded_at,
                        quota_units)
    SELECT youtube_id, item_key, fingerprint, privacy, publish_at, uploaded_at, quota_units
    FROM uploads;
DROP TABLE uploads;
ALTER TABLE uploads_v3 RENAME TO uploads;
CREATE INDEX idx_uploads_item ON uploads(item_key);
CREATE INDEX idx_uploads_publish_at ON uploads(publish_at);
CREATE INDEX idx_uploads_fingerprint ON uploads(fingerprint);
CREATE INDEX idx_uploads_content ON uploads(content_sha256);
UPDATE uploads SET uploaded_at = replace(uploaded_at, ' ', 'T') || '+00:00'
    WHERE uploaded_at NOT LIKE '%+%' AND uploaded_at NOT LIKE '%Z';
UPDATE review_queue SET kind = 'claim' WHERE reason LIKE 'Content-ID claim%';
UPDATE items SET status = 'claimed'
    WHERE key IN (SELECT source_id FROM review_queue WHERE kind = 'claim')
"""


def normalize_caption(caption: str) -> str:
    """Lowercase, drop punctuation/emoji and collapse whitespace."""
    text = re.sub(r"[^\w\s]", " ", caption.lower())
    return " ".join(text.split())


def catalog_fingerprint(entry: CatalogEntry) -> str:
    """Soft cross-platform hint (caption + duration) used to prefer one platform's copy.

    Never used alone to decide that a video was uploaded: captions differ between platforms
    and emoji-only captions collide. The upload dedupe key is the source file's sha256.
    """
    key = f"{normalize_caption(entry.caption)}|{round(entry.duration)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _dt(text: str | None) -> datetime | None:
    """Parse a stored timestamp; legacy naive values are UTC (SQLite CURRENT_TIMESTAMP)."""
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _run_script(c: sqlite3.Connection, script: str) -> None:
    """Execute ``;``-separated statements inside the current transaction (unlike executescript)."""
    for statement in script.split(";"):
        if statement.strip():
            c.execute(statement)


def _migrate_v1(c: sqlite3.Connection) -> None:
    _run_script(c, _V1_LEGACY)


def _migrate_v2(c: sqlite3.Connection) -> None:
    """Introduce items/uploads/quota/caches; carry over legacy catalog, reviews and ledger."""
    _run_script(c, _V2_TABLES)
    for row in c.execute("SELECT * FROM catalog").fetchall():
        fp = catalog_fingerprint(_entry(row))
        c.execute(
            "INSERT INTO items (key, kind, fingerprint, status) VALUES (?, 'catalog', ?, ?)",
            (row["source_id"], fp, row["status"]),
        )
    c.execute("ALTER TABLE catalog DROP COLUMN status")
    c.execute(
        """INSERT INTO uploads (youtube_id, item_key, fingerprint, privacy, uploaded_at)
           SELECT youtube_id, source_id, fingerprint, 'unlisted', created FROM ledger"""
    )
    c.execute("DROP TABLE ledger")
    c.execute("UPDATE review_queue SET kind='audio' WHERE reason LIKE 'licensed%'")
    c.execute("UPDATE review_queue SET kind='ambiguous' WHERE reason LIKE 'ambiguous%'")
    c.execute(
        "DELETE FROM review_queue "
        "WHERE id NOT IN (SELECT MAX(id) FROM review_queue GROUP BY source_id)"
    )
    c.execute("CREATE UNIQUE INDEX idx_review_item ON review_queue(source_id)")


def _migrate_v3(c: sqlite3.Connection) -> None:
    """Dedupe uploads by source content hash (caption fingerprint no longer unique), store
    timestamps as ISO UTC and map legacy Content-ID reviews to claims."""
    _run_script(c, _V3_UPLOADS)


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_v1, _migrate_v2, _migrate_v3]
SCHEMA_VERSION = len(MIGRATIONS)


def _entry(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(
        platform=row["platform"],
        source_id=row["source_id"],
        url=row["url"],
        caption=row["caption"],
        duration=row["duration"],
        view_count=row["view_count"],
        like_count=row["like_count"],
        comment_count=row["comment_count"],
        upload_date=row["upload_date"],
        track=row["track"],
        artist=row["artist"],
        raw=json.loads(row["raw"]),
    )


class Store:
    """Repository over the SQLite database (one short-lived connection per operation)."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ schema
    def init_schema(self) -> None:
        """Apply pending migrations."""
        with self._tx() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            for number, migrate in enumerate(MIGRATIONS[version:], start=version + 1):
                c.execute("BEGIN")  # one atomic transaction per migration step
                migrate(c)
                c.execute(f"PRAGMA user_version = {number}")
                c.commit()

    def schema_version(self) -> int:
        """Current ``PRAGMA user_version``."""
        with self._tx() as c:
            return int(c.execute("PRAGMA user_version").fetchone()[0])

    # ----------------------------------------------------------------- catalog
    def upsert_catalog(self, e: CatalogEntry) -> None:
        """Insert or refresh a discovered post and make sure it has a work item."""
        with self._tx() as c:
            c.execute(
                """INSERT INTO catalog (source_id, platform, url, caption, duration, view_count,
                       like_count, comment_count, upload_date, track, artist, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET
                       platform=excluded.platform, url=excluded.url, caption=excluded.caption,
                       duration=excluded.duration, view_count=excluded.view_count,
                       like_count=excluded.like_count, comment_count=excluded.comment_count,
                       upload_date=excluded.upload_date, track=excluded.track,
                       artist=excluded.artist, raw=excluded.raw""",
                (
                    e.source_id, e.platform.value, e.url, e.caption, e.duration, e.view_count,
                    e.like_count, e.comment_count, e.upload_date, e.track, e.artist,
                    json.dumps(e.raw),
                ),
            )  # fmt: skip
            c.execute(
                """INSERT INTO items (key, kind, fingerprint) VALUES (?, 'catalog', ?)
                   ON CONFLICT(key) DO UPDATE SET fingerprint=excluded.fingerprint""",
                (e.source_id, catalog_fingerprint(e)),
            )

    def catalog_entries(self) -> list[CatalogEntry]:
        """All discovered posts, in discovery order."""
        with self._tx() as c:
            rows = c.execute("SELECT * FROM catalog ORDER BY rowid").fetchall()
        return [_entry(r) for r in rows]

    # ------------------------------------------------------------------- items
    def upsert_raw(self, key: str, sha256: str, path: str) -> None:
        """Register an inbox file (content-deduped by key); refresh its path if it moved."""
        with self._tx() as c:
            c.execute(
                """INSERT INTO items (key, kind, fingerprint, source_path) VALUES (?, 'raw', ?, ?)
                   ON CONFLICT(key) DO UPDATE SET source_path=excluded.source_path""",
                (key, sha256, path),
            )

    def items(self, kind: SourceKind | None = None) -> list[Item]:
        """Work items (optionally of one kind), with catalog metadata joined in."""
        sql = "SELECT i.*, c.* FROM items i LEFT JOIN catalog c ON c.source_id = i.key"
        args: tuple = ()
        if kind:
            sql, args = sql + " WHERE i.kind = ?", (kind.value,)
        with self._tx() as c:
            rows = c.execute(sql + " ORDER BY i.rowid", args).fetchall()
        return [self._item(r) for r in rows]

    def item(self, key: str) -> Item | None:
        """One work item by key."""
        with self._tx() as c:
            row = c.execute(
                "SELECT i.*, c.* FROM items i LEFT JOIN catalog c ON c.source_id = i.key "
                "WHERE i.key = ?",
                (key,),
            ).fetchone()
        return self._item(row) if row else None

    @staticmethod
    def _item(r: sqlite3.Row) -> Item:
        return Item(
            key=r["key"],
            kind=r["kind"],
            fingerprint=r["fingerprint"],
            status=r["status"],
            source_path=r["source_path"],
            attempts=r["attempts"],
            entry=_entry(r) if r["source_id"] else None,
        )

    def set_status(self, key: str, status: ItemStatus) -> None:
        """Change an item's status."""
        self._update(key, "status = ?", ItemStatus(status).value)

    def set_source(self, key: str, path: str) -> None:
        """Remember which file to render for an item."""
        self._update(key, "source_path = ?", path)

    def add_attempt(self, key: str) -> int:
        """Count one failed attempt; return the new total."""
        self._update(key, "attempts = attempts + 1")
        item = self.item(key)
        return item.attempts if item else 0

    def reset_attempts(self, key: str) -> None:
        """Give a (human-approved) item a fresh retry budget."""
        self._update(key, "attempts = 0")

    def _update(self, key: str, assignment: str, *args: object) -> None:
        with self._tx() as c:
            c.execute(
                f"UPDATE items SET {assignment}, updated = CURRENT_TIMESTAMP WHERE key = ?",
                (*args, key),
            )

    def status_counts(self) -> dict[tuple[str, str], int]:
        """Item counts keyed by (kind, status)."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT kind, status, COUNT(*) AS n FROM items GROUP BY kind, status"
            ).fetchall()
        return {(r["kind"], r["status"]): r["n"] for r in rows}

    # ----------------------------------------------------------------- matches
    def set_match(self, m: MatchResult) -> None:
        """Store a match outcome (and its candidates, for ambiguous reviews)."""
        with self._tx() as c:
            c.execute(
                """INSERT INTO matches (source_id, original_path, confidence, status, candidates)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET original_path=excluded.original_path,
                       confidence=excluded.confidence, status=excluded.status,
                       candidates=excluded.candidates""",
                (m.source_id, m.original_path, m.confidence, m.status.value,
                 json.dumps(m.candidates)),
            )  # fmt: skip

    def match_candidates(self, key: str) -> list[str]:
        """Candidate originals recorded for an ambiguous match."""
        with self._tx() as c:
            row = c.execute("SELECT candidates FROM matches WHERE source_id = ?", (key,)).fetchone()
        return json.loads(row["candidates"]) if row else []

    # ----------------------------------------------------------------- reviews
    def add_review(self, key: str, kind: ReviewKind, reason: str) -> None:
        """Put an item in the review queue (one row per item, latest reason wins)."""
        with self._tx() as c:
            c.execute(
                """INSERT INTO review_queue (source_id, kind, reason) VALUES (?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET kind=excluded.kind,
                       reason=excluded.reason, created=CURRENT_TIMESTAMP""",
                (key, ReviewKind(kind).value, reason),
            )

    def reviews(self) -> list[dict]:
        """The review queue, oldest first."""
        with self._tx() as c:
            rows = c.execute("SELECT * FROM review_queue ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def review(self, key: str) -> dict | None:
        """The review row of one item, if any."""
        with self._tx() as c:
            row = c.execute("SELECT * FROM review_queue WHERE source_id = ?", (key,)).fetchone()
        return dict(row) if row else None

    def resolve_review(self, key: str) -> None:
        """Remove an item from the review queue."""
        with self._tx() as c:
            c.execute("DELETE FROM review_queue WHERE source_id = ?", (key,))

    # ----------------------------------------------------------------- uploads
    def record_upload(self, u: Upload) -> None:
        """Write the ledger row of a finished upload (call right after the API returns)."""
        with self._tx() as c:
            c.execute(
                """INSERT INTO uploads (youtube_id, item_key, fingerprint, content_sha256,
                       privacy, publish_at, uploaded_at, quota_units) VALUES (?,?,?,?,?,?,?,?)""",
                (u.youtube_id, u.item_key, u.fingerprint, u.content_sha256, u.privacy,
                 _iso(u.publish_at) if u.publish_at else None, _iso(u.uploaded_at),
                 u.quota_units),
            )  # fmt: skip

    def has_upload(self, item_key: str) -> bool:
        """True if this item was already uploaded."""
        return self._exists("SELECT 1 FROM uploads WHERE item_key = ?", item_key)

    def is_content_uploaded(self, sha256: str) -> bool:
        """True if a video rendered from this exact source file is already on YouTube."""
        return self._exists("SELECT 1 FROM uploads WHERE content_sha256 = ?", sha256)

    def is_legacy_uploaded(self, fingerprint: str) -> bool:
        """True if a pre-v3 upload (no content hash) carries this caption fingerprint."""
        return self._exists(
            "SELECT 1 FROM uploads WHERE content_sha256 IS NULL AND fingerprint = ?", fingerprint
        )

    def _exists(self, sql: str, *args: object) -> bool:
        with self._tx() as c:
            return c.execute(sql, args).fetchone() is not None

    def uploads(self, since: datetime | None = None) -> list[Upload]:
        """Ledger rows, oldest first (optionally only those uploaded since ``since``)."""
        sql, args = "SELECT * FROM uploads", ()
        if since:
            sql, args = sql + " WHERE uploaded_at >= ?", (_iso(since),)
        with self._tx() as c:
            rows = c.execute(sql + " ORDER BY uploaded_at, item_key", args).fetchall()
        return [
            Upload(
                youtube_id=r["youtube_id"],
                item_key=r["item_key"],
                fingerprint=r["fingerprint"],
                content_sha256=r["content_sha256"],
                privacy=r["privacy"],
                publish_at=_dt(r["publish_at"]),
                uploaded_at=_dt(r["uploaded_at"]),
                quota_units=r["quota_units"],
            )
            for r in rows
        ]

    def publish_times(self) -> list[datetime]:
        """All scheduled publication instants (UTC), sorted."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT publish_at FROM uploads WHERE publish_at IS NOT NULL ORDER BY publish_at"
            ).fetchall()
        return [datetime.fromisoformat(r["publish_at"]) for r in rows]

    def set_upload_privacy(self, youtube_id: str, privacy: str) -> None:
        """Reflect a privacy change made on YouTube (clears any pending schedule)."""
        with self._tx() as c:
            c.execute(
                "UPDATE uploads SET privacy = ?, publish_at = NULL WHERE youtube_id = ?",
                (privacy, youtube_id),
            )

    # ------------------------------------------------------------------- quota
    def spend_quota(self, day: str, units: int, uploads: int = 0) -> None:
        """Add API units (and ``videos.insert`` calls) spent on a (Pacific) quota day."""
        with self._tx() as c:
            c.execute(
                """INSERT INTO quota_usage (day, units, uploads) VALUES (?, ?, ?)
                   ON CONFLICT(day) DO UPDATE SET units = units + excluded.units,
                       uploads = uploads + excluded.uploads""",
                (day, units, uploads),
            )

    def quota_used(self, day: str) -> tuple[int, int]:
        """(units, uploads) spent on a (Pacific) quota day."""
        with self._tx() as c:
            row = c.execute(
                "SELECT units, uploads FROM quota_usage WHERE day = ?", (day,)
            ).fetchone()
        return (int(row["units"]), int(row["uploads"])) if row else (0, 0)

    # ------------------------------------------------------------------ caches
    def cached_original(self, path: str, size: int) -> tuple[float, list[str]] | None:
        """(duration, phashes) of an original if cached for this exact file size."""
        with self._tx() as c:
            row = c.execute(
                "SELECT duration, phashes FROM originals WHERE path = ? AND size = ?", (path, size)
            ).fetchone()
        return (row["duration"], json.loads(row["phashes"])) if row else None

    def cache_original(self, path: str, size: int, duration: float, phashes: list[str]) -> None:
        """Cache the duration and phashes of an original file."""
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO originals (path, size, duration, phashes) VALUES (?,?,?,?)",
                (path, size, duration, json.dumps(phashes)),
            )

    def cached_ref(self, key: str) -> list[str] | None:
        """Cached pHashes of a post's downloaded reference clip."""
        with self._tx() as c:
            row = c.execute("SELECT phashes FROM ref_phashes WHERE key = ?", (key,)).fetchone()
        return json.loads(row["phashes"]) if row else None

    def cache_ref(self, key: str, phashes: list[str]) -> None:
        """Cache a reference clip's pHashes (so pending posts are never re-downloaded)."""
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO ref_phashes (key, phashes) VALUES (?, ?)",
                (key, json.dumps(phashes)),
            )

    def cached_hash(self, path: str, size: int, mtime_ns: int) -> str | None:
        """Cached sha256 of a file, valid only if size and mtime are unchanged."""
        with self._tx() as c:
            row = c.execute(
                "SELECT sha256 FROM file_hashes WHERE path = ? AND size = ? AND mtime_ns = ?",
                (path, size, mtime_ns),
            ).fetchone()
        return row["sha256"] if row else None

    def cache_hash(self, path: str, size: int, mtime_ns: int, sha256: str) -> None:
        """Cache a file's sha256."""
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO file_hashes (path, size, mtime_ns, sha256) "
                "VALUES (?,?,?,?)",
                (path, size, mtime_ns, sha256),
            )

    # ------------------------------------------------------------------ alerts
    def add_alert(self, message: str) -> None:
        """Record an operator alert (shown by ``status`` until resolved)."""
        with self._tx() as c:
            c.execute("INSERT INTO alerts (message) VALUES (?)", (message,))

    def open_alerts(self) -> list[dict]:
        """Unresolved alerts, oldest first."""
        with self._tx() as c:
            rows = c.execute("SELECT * FROM alerts WHERE resolved = 0 ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def resolve_alerts(self) -> None:
        """Mark every alert as handled."""
        with self._tx() as c:
            c.execute("UPDATE alerts SET resolved = 1 WHERE resolved = 0")
