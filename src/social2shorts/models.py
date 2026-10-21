"""
Data models for social2shorts.

This module defines the Pydantic models and Enums used throughout the pipeline
to represent catalog entries, original videos, match results, and metadata.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """
    Supported social media platforms.
    """
    tiktok = "tiktok"
    instagram = "instagram"


class ItemStatus(str, Enum):
    """
    Statuses for a video in the processing pipeline.
    """
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
    """
    Information about a video discovered on a social platform.

    Attributes:
        platform (Platform): The platform where the video was found.
        source_id (str): The platform-specific ID of the video.
        url (str): URL to the video.
        caption (str): The original caption.
        duration (float): Video duration in seconds.
        view_count (int): Number of views.
        like_count (int): Number of likes.
        comment_count (int): Number of comments.
        upload_date (str): The upload date string.
        track (str): The track name.
        artist (str): The artist name.
        raw (dict): Raw info dictionary from yt-dlp.
    """
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
        """
        Check if the video uses original audio.

        Returns:
            bool: True if the track is empty or "original sound".
        """
        return self.track.strip().lower() in {"", "original sound"}


class Original(BaseModel):
    """
    Represents a high-quality original video file.

    Attributes:
        path (str): Path to the local file.
        duration (float): Video duration in seconds.
        phashes (list[str]): List of perceptual hashes for the video.
    """
    path: str
    duration: float
    phashes: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    """
    The result of matching a catalog entry to an original video.

    Attributes:
        source_id (str): The ID of the source video.
        original_path (str | None): Path to the matched original file, or None.
        confidence (float): Match confidence score (0.0 to 1.0).
        status (ItemStatus): The resulting status (e.g., matched, ambiguous).
    """
    source_id: str
    original_path: str | None
    confidence: float
    status: ItemStatus


class ShortMeta(BaseModel):
    """
    YouTube Shorts metadata.

    Attributes:
        title (str): The video title.
        description (str): The video description.
        tags (list[str]): A list of tags.
    """
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
