from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class Claim:
    reason: str
    severity: str

def check_claims(video_id: str, service: Any) -> list[Claim]:
    """
    Checks for Content-ID claims on a video.
    Since the YouTube Data API v3 does not provide direct access to Content-ID claims
    (which are managed via Content ID/CMS), this is a placeholder for the logic
    that would typically integrate with a CMS API or a specific monitoring tool.
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
    Checks for claims and triggers on_claim + sets video to private if claims are found.
    """
    claims = check_claims(video_id, service)
    if claims:
        import insta2youtube.youtube_upload as youtube_upload
        # Set to private immediately
        youtube_upload.set_privacy(video_id, "private", service=service)

        # Notify the caller (e.g., to flag for review in DB)
        reason = "; ".join(c.reason for c in claims)
        on_claim(reason)
