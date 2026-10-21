"""YouTube upload: OAuth credentials + resumable videos.insert."""
from __future__ import annotations

import os
import warnings
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from social2shorts.models import ShortMeta

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
# "2" = Autos & Vehicles (motorcycle content).
CATEGORY_ID = "2"


class AuthRefreshError(Exception):
    """Stored token could not be refreshed; re-run interactive auth."""


def get_credentials(client_secrets: str, token_path: str) -> Credentials:
    """Manage YouTube OAuth credentials, including loading and refreshing.

    Args:
        client_secrets: Path to the client secrets JSON file.
        token_path: Path where the authorized token is stored.

    Returns:
        A valid Google Credentials object.

    Raises:
        AuthRefreshError: If the token is corrupt or cannot be refreshed.
    """
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as exc:  # corrupt JSON, missing fields, unreadable
            raise AuthRefreshError(f"stored token unusable: {exc}") from exc
    if creds is not None and creds.valid:
        return creds
    if creds is not None and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise AuthRefreshError(f"token refresh failed: {exc}") from exc
        _save(creds, token_path)
        return creds
    if creds is not None:
        raise AuthRefreshError("stored token has no refresh token")
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
    creds = flow.run_local_server(port=0)
    _save(creds, token_path)
    return creds


def _save(creds: Credentials, token_path: str) -> None:
    """Save credentials to a file with restricted permissions.

    Args:
        creds: The credentials to save.
        token_path: Path to the token file.
    """
    # Token holds the refresh token (a secret): owner-only perms.
    # On Windows 0o600 is best-effort (POSIX mode maps to the read-only
    # attribute, not full ACLs); correct on POSIX, harmless on Windows.
    d = os.path.dirname(os.path.abspath(token_path))
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    try:
        os.chmod(token_path, 0o600)  # tighten an already-existing looser file
    except OSError:
        warnings.warn(f"could not tighten permissions on {token_path}")


def build_service(client_secrets: str = "secrets/client_secret.json",
                  token_path: str = "secrets/token.json") -> Any:
    """Create a YouTube API service object.

    Args:
        client_secrets: Path to the client secrets JSON file.
        token_path: Path to the token JSON file.

    Returns:
        A Google API client service object for YouTube v3.
    """
    from googleapiclient.discovery import build

    creds = get_credentials(client_secrets, token_path)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def build_body(meta: ShortMeta, privacy: str) -> dict:
    """Construct the request body for a YouTube video upload.

    Args:
        meta: The metadata for the short.
        privacy: The privacy status (e.g., 'public', 'private', 'unlisted').

    Returns:
        A dictionary formatted for the YouTube API videos.insert method.
    """
    return {
        "snippet": {
            "title": meta.title,
            "description": meta.description,
            "tags": list(meta.tags),
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }


def upload_short(video_path: str, meta: ShortMeta, privacy: str, service=None) -> str:
    """Upload a video as a YouTube Short using a resumable upload.

    Args:
        video_path: Path to the video file.
        meta: The metadata for the upload.
        privacy: The privacy status for the video.
        service: Optional YouTube API service object. If None, one is created.

    Returns:
        The ID of the uploaded YouTube video.
    """
    from googleapiclient.http import MediaFileUpload

    service = service or build_service()
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = service.videos().insert(
        part="snippet,status", body=build_body(meta, privacy), media_body=media
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def set_privacy(video_id: str, privacy: str, service=None) -> None:
    """Update the privacy status of an existing YouTube video.

    Args:
        video_id: The ID of the YouTube video.
        privacy: The new privacy status.
        service: Optional YouTube API service object. If None, one is created.
    """
    service = service or build_service()
    service.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy,
                                         "selfDeclaredMadeForKids": False}},
    ).execute()
