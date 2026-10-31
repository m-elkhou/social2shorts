"""Domain models shared by every stage: catalog posts, work items, matches and metadata."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from social2shorts.montage.types import Word


class Platform(StrEnum):
    """Social platforms the catalog is discovered from."""

    tiktok = "tiktok"
    instagram = "instagram"


class SourceKind(StrEnum):
    """Where a work item comes from."""

    catalog = "catalog"  # already-published reel, rendered from the phone original
    raw = "raw"  # never-published footage dropped in the inbox folder


class Privacy(StrEnum):
    """Upload visibility. ``scheduled`` = private now, public at the next free publish slot."""

    public = "public"
    unlisted = "unlisted"
    private = "private"
    scheduled = "scheduled"


class ItemStatus(StrEnum):
    """Lifecycle of a work item."""

    discovered = "discovered"
    pending_original = "pending_original"  # no phone original matched yet (retried each run)
    matched = "matched"
    ambiguous = "ambiguous"  # several originals match: a human must pick one
    review_audio = "review_audio"  # licensed music: a human must approve
    ready = "ready"  # approved by a human: processed first on the next run
    failed = "failed"  # processing error: retried until MAX_ATTEMPTS, then reviewed
    too_short = "too_short"  # raw clip below min_duration_s: a human decides
    rendered = "rendered"  # dry run output only
    uploading = "uploading"  # API call in flight; left behind only if the run crashed
    uploaded = "uploaded"
    duplicate = "duplicate"  # same content already uploaded (e.g. the TikTok copy of an IG post)
    claimed = "claimed"  # uploaded, then flagged by the Content-ID guard
    rejected = "rejected"  # a human said no: never processed again


class ReviewKind(StrEnum):
    """Why an item is waiting for a human."""

    audio = "audio"
    ambiguous = "ambiguous"
    error = "error"
    claim = "claim"
    short = "short"
    interrupted = "interrupted"  # crash during upload: check YouTube Studio before retrying


class CatalogEntry(BaseModel):
    """A post discovered on TikTok or Instagram."""

    platform: Platform
    source_id: str
    url: str
    caption: str
    duration: float
    view_count: int
    like_count: int
    comment_count: int
    upload_date: str
    track: str
    artist: str
    raw: dict = Field(default_factory=dict)

    @property
    def is_original_audio(self) -> bool:
        """True when the post uses its own sound (no licensed track)."""
        return self.track.strip().lower() in {"", "original sound", "son original"}


class Item(BaseModel):
    """One video to turn into a Short, whatever its source.

    Attributes:
        key: Stable id (catalog: platform post id; raw: ``raw-<sha256 prefix>``).
        kind: Source kind, selects the preparation strategy.
        fingerprint: Upload dedupe key (caption+duration hash, or file sha256).
        status: Current lifecycle status.
        source_path: File to render, once known (raw file or chosen phone original).
        attempts: Failed processing attempts so far.
        entry: Catalog metadata (catalog items only).
    """

    key: str
    kind: SourceKind
    fingerprint: str
    status: ItemStatus = ItemStatus.discovered
    source_path: str | None = None
    attempts: int = 0
    entry: CatalogEntry | None = None

    @property
    def caption(self) -> str:
        """Original caption, empty for raw footage."""
        return self.entry.caption if self.entry else ""


class Original(BaseModel):
    """A high-quality original video file on local disk."""

    path: str
    duration: float
    phashes: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    """Outcome of matching a catalog post to the phone originals."""

    source_id: str
    original_path: str | None
    confidence: float
    status: ItemStatus
    candidates: list[str] = Field(default_factory=list)


class ShortMeta(BaseModel):
    """YouTube metadata for one Short.

    Attributes:
        title: Video title (<= 100 chars).
        description: Video description (hashtags ending with ``#Shorts`` are added at upload).
        tags: Content-derived tags.
        hook: Punchy on-screen line for the first seconds (<= 40 chars).
    """

    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    hook: str = ""


class Upload(BaseModel):
    """One video uploaded to YouTube (the anti-duplicate ledger row)."""

    youtube_id: str
    item_key: str
    fingerprint: str
    content_sha256: str | None = None  # sha256 of the source file (the real dedupe key)
    privacy: str
    publish_at: datetime | None = None
    uploaded_at: datetime
    quota_units: int = 0


class Rendered(BaseModel):
    """A finished Short and its transcript in the Short's own time (for the subtitle track)."""

    path: Path
    words: list[Word] = Field(default_factory=list)


class UploadResult(BaseModel):
    """What YouTube actually applied to a new upload (may differ from what was asked)."""

    video_id: str
    privacy: str
    publish_at: datetime | None = None


class Claim(BaseModel):
    """A post-upload problem reported by the YouTube Data API."""

    video_id: str
    reason: str
