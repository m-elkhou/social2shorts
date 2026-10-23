"""Post-upload rights check using what the public YouTube Data API actually exposes.

Limitation: real Content ID claims are only visible in YouTube Studio or to CMS partners
(Content ID API); the Data API has no "claims" endpoint. We therefore flag the signals it does
give: rejected/failed uploads, region blocking (the usual effect of a music claim) and
``licensedContent``. Claims also appear minutes to hours after upload, which is why the
pipeline re-checks recent uploads at the start of every run, not only right after upload.
"""

from __future__ import annotations

from typing import Any

from social2shorts.models import Claim

BATCH = 50  # videos.list accepts up to 50 ids for 1 quota unit


def claims_from_item(item: dict) -> list[Claim]:
    """Problems visible on one ``videos.list(part="status,contentDetails")`` item."""
    vid = item.get("id", "")
    status = item.get("status", {})
    details = item.get("contentDetails", {})
    claims = []
    if status.get("uploadStatus") == "rejected":
        claims.append(Claim(video_id=vid, reason=f"rejected: {status.get('rejectionReason', '?')}"))
    if status.get("uploadStatus") == "failed":
        claims.append(Claim(video_id=vid, reason=f"failed: {status.get('failureReason', '?')}"))
    region = details.get("regionRestriction", {})
    if region.get("blocked"):
        claims.append(Claim(video_id=vid, reason=f"blocked in {len(region['blocked'])} countries"))
    if "allowed" in region:  # an allow-list (even empty) means viewable only in those countries
        claims.append(Claim(video_id=vid, reason=f"only viewable in {len(region['allowed'])} countries"))
    if details.get("licensedContent"):
        claims.append(Claim(video_id=vid, reason="claimed as licensed content"))
    return claims


def check_claims(service: Any, video_ids: list[str]) -> list[Claim]:
    """Check uploaded videos (1 quota unit per 50 ids); a vanished video is a claim too."""
    claims: list[Claim] = []
    for start in range(0, len(video_ids), BATCH):
        batch = video_ids[start : start + BATCH]
        resp = (
            service.videos()
            .list(part="status,contentDetails", id=",".join(batch))
            .execute(num_retries=3)
        )
        items = resp.get("items", [])
        for item in items:
            claims += claims_from_item(item)
        found = {i.get("id") for i in items}
        claims += [Claim(video_id=v, reason="removed by YouTube") for v in batch if v not in found]
    return claims
