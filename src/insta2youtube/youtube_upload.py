"""YouTube upload: OAuth credentials + resumable videos.insert."""
from __future__ import annotations

import os
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from insta2youtube.models import ShortMeta

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
# "2" = Autos & Vehicles (motorcycle content).
CATEGORY_ID = "2"


class AuthRefreshError(Exception):
    """Stored token could not be refreshed; re-run interactive auth."""


def get_credentials(client_secrets: str, token_path: str) -> Credentials:
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
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
        pass


def build_service(client_secrets: str = "secrets/client_secret.json",
                  token_path: str = "secrets/token.json") -> Any:
    from googleapiclient.discovery import build

    creds = get_credentials(client_secrets, token_path)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def build_body(meta: ShortMeta, privacy: str) -> dict:
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
    service = service or build_service()
    service.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy,
                                         "selfDeclaredMadeForKids": False}},
    ).execute()
