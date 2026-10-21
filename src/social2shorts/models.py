from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    tiktok = "tiktok"
    instagram = "instagram"


class ItemStatus(str, Enum):
    discovered = "discovered"
    curated = "curated"
    pending_original = "pending_original"
    matched = "matched"
    ambiguous = "ambiguous"
    review_audio = "review_audio"
    ready = "ready"
    uploaded = "uploaded"
    skipped = "skipped"


class CatalogEntry(BaseModel):
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
        return self.track.strip().lower() in {"", "original sound"}


class Original(BaseModel):
    path: str
    duration: float
    phashes: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    source_id: str
    original_path: str | None
    confidence: float
    status: ItemStatus


class ShortMeta(BaseModel):
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
