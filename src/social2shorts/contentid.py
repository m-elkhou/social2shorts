"""
Content-ID guard module.

This module provides tools to check for copyright claims on uploaded YouTube
videos and automatically privatize them if claims are detected.
"""
from __future__ import annotations

from typing import Any, Callable
from dataclasses import dataclass

from social2shorts.models import CatalogEntry, ShortMeta


@dataclass
class Claim:
    """
    Represents a copyright claim.

    Attributes:
        reason (str): The reason for the claim.
        severity (str): The severity level of the claim.
    """
    reason: str
    severity: str


def check_claims(video_id: str, service: Any) -> list[Claim]:
    """
    Check for Content-ID claims on a video.

    Since the YouTube Data API v3 does not provide direct access to Content-ID claims
    (which are managed via Content ID/CMS), this is a placeholder for the logic
    that would typically integrate with a CMS API or a specific monitoring tool.

    Args:
        video_id (str): The YouTube video ID.
        service (Any): The YouTube API service instance.

    Returns:
        list[Claim]: A list of detected claims.
    """
    # In a real scenario, you would call the CMS API or a specialized endpoint.
    # For the purpose of this task, we return an empty list by default.
    return []


def guard_after_upload(
    video_id: str,
    on_claim: Callable[[str], None],
    service: Any
) -> None:
    """
    Check for claims and trigger on_claim + set video to private if claims are found.

    Args:
        video_id (str): The YouTube video ID.
        on_claim (Callable[[str], None]): Callback triggered when a claim is found,
            receiving the claim reason as an argument.
        service (Any): The YouTube API service instance.
    """
    claims = check_claims(video_id, service)
    if claims:
        import social2shorts.youtube_upload as youtube_upload
        # Set to private immediately
        youtube_upload.set_privacy(video_id, "private", service=service)

        # Notify the caller (e.g., to flag for review in DB)
        reason = "; ".join(c.reason for c in claims)
        on_claim(reason)
