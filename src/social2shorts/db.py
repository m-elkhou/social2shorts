"""
Database management module.

This module provides the Store class for persisting the video catalog,
matching results, review queue, and upload ledger using SQLite.
"""
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
    """
    Lowercase, drop punctuation/emoji, and collapse whitespace in a caption.

    Args:
        caption (str): The raw caption text.

    Returns:
        str: The normalized caption.
    """
    text = re.sub(r"[^\w\s]", " ", caption.lower())
    return " ".join(text.split())


class Store:
    """
    SQLite store for social2shorts data.

    Attributes:
        db_path (str): Path to the SQLite database file.
    """
    def __init__(self, db_path: str):
        """
        Initialize the store.

        Args:
            db_path (str): Path to the SQLite database file.
        """
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        """
        Create a database connection with Row factory.

        Returns:
            sqlite3.Connection: The database connection.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Initialize the database schema by executing the predefined SQL script."""
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def upsert_catalog(self, e: CatalogEntry) -> None:
        """
        Insert or update a catalog entry.

        Args:
            e (CatalogEntry): The catalog entry to upsert.
        """
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
        """
        Retrieve all entries in the catalog.

        Returns:
            list[CatalogEntry]: A list of all catalog entries.
        """
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
        """
        Update the status of a catalog entry.

        Args:
            source_id (str): The ID of the source video.
            status (ItemStatus): The new status to assign.
        """
        with self._conn() as c:
            c.execute("UPDATE catalog SET status=? WHERE source_id=?",
                      (ItemStatus(status).value, source_id))

    def set_match(self, m: MatchResult) -> None:
        """
        Upsert a match result into the database.

        Args:
            m (MatchResult): The match result to store.
        """
        with self._conn() as c:
            c.execute(
                """INSERT INTO matches (source_id, original_path, confidence, status)
                   VALUES (?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET original_path=excluded.original_path,
                       confidence=excluded.confidence, status=excluded.status""",
                (m.source_id, m.original_path, m.confidence, m.status.value),
            )

    def add_review(self, source_id: str, reason: str) -> None:
        """
        Add an item to the review queue.

        Args:
            source_id (str): The ID of the source video.
            reason (str): The reason the video requires review.
        """
        with self._conn() as c:
            c.execute("INSERT INTO review_queue (source_id, reason) VALUES (?,?)",
                      (source_id, reason))

    def reviews(self) -> list[dict]:
        """
        Retrieve all items currently in the review queue.

        Returns:
            list[dict]: A list of review items as dictionaries.
        """
        with self._conn() as c:
            rows = c.execute("SELECT * FROM review_queue ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def resolve_review(self, source_id: str) -> None:
        """
        Remove an item from the review queue.

        Args:
            source_id (str): The ID of the source video to resolve.
        """
        with self._conn() as c:
            c.execute("DELETE FROM review_queue WHERE source_id=?", (source_id,))

    def mark_uploaded(self, fingerprint: str, source_id: str, youtube_id: str) -> None:
        """
        Record a successful upload in the ledger.

        Args:
            fingerprint (str): The fingerprint of the video.
            source_id (str): The ID of the source video.
            youtube_id (str): The resulting YouTube video ID.
        """
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ledger (fingerprint, source_id, youtube_id) VALUES (?,?,?)",
                (fingerprint, source_id, youtube_id),
            )

    def is_uploaded(self, fingerprint: str) -> bool:
        """
        Check if a video with the given fingerprint has been uploaded.

        Args:
            fingerprint (str): The fingerprint to check.

        Returns:
            bool: True if uploaded, False otherwise.
        """
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM ledger WHERE fingerprint=?", (fingerprint,)).fetchone()
        return row is not None

    def fingerprint(self, entry: CatalogEntry) -> str:
        """
        Generate a content-based fingerprint for a catalog entry.

        Args:
            entry (CatalogEntry): The entry to fingerprint.

        Returns:
            str: A SHA-256 hash of the normalized caption and duration.
        """
        key = f"{normalize_caption(entry.caption)}|{round(entry.duration)}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    def status_counts(self) -> dict[str, int]:
        """
        Get the number of entries for each status.

        Returns:
            dict[str, int]: A mapping of status strings to their counts.
        """
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) AS n FROM catalog GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def ledger(self) -> list[dict]:
        """
        Retrieve all upload records from the ledger.

        Returns:
            list[dict]: A list of upload records as dictionaries.
        """
        with self._conn() as c:
            rows = c.execute("SELECT * FROM ledger ORDER BY created, source_id").fetchall()
        return [dict(r) for r in rows]
