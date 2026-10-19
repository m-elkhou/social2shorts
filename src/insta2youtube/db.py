from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .models import CatalogEntry, ItemStatus, MatchResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    source_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    caption TEXT NOT NULL,
    duration REAL NOT NULL,
    view_count INTEGER NOT NULL,
    like_count INTEGER NOT NULL,
    comment_count INTEGER NOT NULL,
    upload_date TEXT NOT NULL,
    track TEXT NOT NULL,
    artist TEXT NOT NULL,
    raw TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'discovered'
);
CREATE TABLE IF NOT EXISTS matches (
    source_id TEXT PRIMARY KEY,
    original_path TEXT,
    confidence REAL NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ledger (
    fingerprint TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    youtube_id TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def normalize_caption(caption: str) -> str:
    """Lowercase, drop punctuation/emoji, collapse whitespace."""
    text = re.sub(r"[^\w\s]", " ", caption.lower())
    return " ".join(text.split())


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def upsert_catalog(self, e: CatalogEntry) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO catalog (source_id, platform, url, caption, duration,
                       view_count, like_count, comment_count, upload_date, track, artist, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET
                       platform=excluded.platform, url=excluded.url, caption=excluded.caption,
                       duration=excluded.duration, view_count=excluded.view_count,
                       like_count=excluded.like_count, comment_count=excluded.comment_count,
                       upload_date=excluded.upload_date, track=excluded.track,
                       artist=excluded.artist, raw=excluded.raw""",
                (e.source_id, e.platform.value, e.url, e.caption, e.duration,
                 e.view_count, e.like_count, e.comment_count, e.upload_date,
                 e.track, e.artist, json.dumps(e.raw)),
            )

    def all_catalog(self) -> list[CatalogEntry]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM catalog ORDER BY rowid").fetchall()
        return [
            CatalogEntry(
                platform=r["platform"], source_id=r["source_id"], url=r["url"],
                caption=r["caption"], duration=r["duration"], view_count=r["view_count"],
                like_count=r["like_count"], comment_count=r["comment_count"],
                upload_date=r["upload_date"], track=r["track"], artist=r["artist"],
                raw=json.loads(r["raw"]),
            )
            for r in rows
        ]

    def set_status(self, source_id: str, status: ItemStatus) -> None:
        with self._conn() as c:
            c.execute("UPDATE catalog SET status=? WHERE source_id=?",
                      (ItemStatus(status).value, source_id))

    def set_match(self, m: MatchResult) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO matches (source_id, original_path, confidence, status)
                   VALUES (?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET original_path=excluded.original_path,
                       confidence=excluded.confidence, status=excluded.status""",
                (m.source_id, m.original_path, m.confidence, m.status.value),
            )

    def add_review(self, source_id: str, reason: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO review_queue (source_id, reason) VALUES (?,?)",
                      (source_id, reason))

    def reviews(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM review_queue ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def resolve_review(self, source_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM review_queue WHERE source_id=?", (source_id,))

    def mark_uploaded(self, fingerprint: str, source_id: str, youtube_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ledger (fingerprint, source_id, youtube_id) VALUES (?,?,?)",
                (fingerprint, source_id, youtube_id),
            )

    def is_uploaded(self, fingerprint: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM ledger WHERE fingerprint=?", (fingerprint,)).fetchone()
        return row is not None

    def fingerprint(self, entry: CatalogEntry) -> str:
        key = f"{normalize_caption(entry.caption)}|{round(entry.duration)}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    def status_counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) AS n FROM catalog GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def ledger(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM ledger ORDER BY created, source_id").fetchall()
        return [dict(r) for r in rows]
